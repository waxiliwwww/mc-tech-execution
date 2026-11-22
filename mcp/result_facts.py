"""Bounded, directly quotable facts from submitted inputs and selected results."""
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def zero_value_note():
    """A tally estimate is not an event counter or a cause diagnosis."""
    return ('零值只表示该计分的统计估计为0；即使均值和标准差均为0，成因也未确定。'
            '不能仅据零值断言没有裂变事件、没有径迹贡献、物理量为零，或认定为统计不足的正常表现。'
            'tracklength计分是径迹估计，不是事件计数。当前未做成因诊断；没有对应证据时不指定成因，'
            '也不增补可能成因清单或零估计概率判断。若需定位，应另取独立诊断证据。')


def material_source_summary(materials):
    """Quote the verified submitted input digest in full, never a current case."""
    if materials.get('status') != 'verified':
        return '材料来源未核实：' + materials.get('reason', '缺少提交输入指纹或核验依据。')
    return ('材料来源文件：' + materials['source_file'] + '\n材料来源文件SHA256：' + materials['sha256'])


def professional_acceptance_status():
    return {'status':'not_performed_by_toolkit', 'result':None,
            'statement':'本工具未执行专业验收；professional_acceptance=false表示本工具未提供专业验收结论，不表示验收失败或不合格。'}


def mesh_indexing_contract(dimension):
    dimension = list(dimension) + [1] * (3-len(dimension))
    return {'dimension':dimension, 'bin_base':1, 'mesh_offset_base':0, 'order':'i_fastest',
            'formula':'(i-1) + nx*(j-1) + nx*ny*(k-1)',
            'first':{'bin':[1,1,1], 'mesh_offset':0},
            'last':{'bin':dimension, 'mesh_offset':math.prod(dimension)-1},
            'scope':'RegularMesh内的0-based箱序号；不是含其他filter、核素和score轴的mean数组扁平序号。'}


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
            'estimator_note': ('反应计分是估计值，不等于本次实际发生事件的计数；tracklength为径迹估计。' if score in ('fission','total') else None),
            'unit_note': ('单位须结合响应函数、导数或multiply_density设置另行核实；未套用普通计分单位。'
                          if modified else 'OpenMC原始计分；未作体积、源强或功率换算。' if unit else
                          '本摘要未定义该score的单位；保留原值，另查专业依据，不从名称猜测。')}


def interpretation(tallies, run_mode, adequacy, input_materials=None):
    scores = []
    for tally in tallies:
        available = tally.get('score_semantics') or [score_semantics(s, tally.get('unit_context')) for s in tally['scores']]
        scores.extend({'tally_id': tally['id'], **item} for item in available)
    applicable = run_mode == 'eigenvalue'
    source = (adequacy or {}).get('source_convergence') or {}
    return {'terminology': 'score是计分类型；nuclide是核素/汇总范围。nuclide=total表示所有核素汇总；'
                          '只有scores中实际出现total时，score=total才表示合法的总反应计分。',
            'scores': scores,
            'professional_acceptance_status': professional_acceptance_status(),
            'mesh_indexing': [{'tally_id':t['id'], **mesh_indexing_contract(t['mesh']['dimension'])}
                              for t in tallies if (t.get('mesh') or {}).get('type')=='RegularMesh'],
            'material_source_summary': material_source_summary(input_materials) if input_materials else None,
            'zero_value_evidence': {'cause_status':'not_determined', 'statement':zero_value_note()},
            'source_convergence': {
                'status': 'not_verified' if applicable or not run_mode else 'not_applicable',
                'statement': ('源收敛未验证；本工具仅作相对σ分级和批次设置提示，未执行源收敛诊断。'
                              if applicable or not run_mode else '该运行模式不适用本征值源收敛判定。'),
                'evidence_limit': '批次阈值不是收敛标准；不能据此判断已收敛或未收敛，也不能断言系统偏差存在、已消除或大小。',
                'batch_advisory': source.get('status', 'not_available')},
            'normalization': '每源粒子归一化的原始计分；未作工程源强/功率换算。source指每个源粒子，不是每秒物理量。'}


def interpretation_summary(facts):
    lines = [facts['terminology']]
    for item in facts['scores']:
        unit = item['raw_unit'] or '未定义（需核实）'
        lines.append(f"tally {item['tally_id']}：score={item['score']}（{item['meaning']}）；原始单位={unit}；{item['unit_note']}")
        if item.get('estimator_note'):lines.append(item['estimator_note'])
    for mesh in facts.get('mesh_indexing', []):
        lines.append(f"tally {mesh['tally_id']}：网格bin=[i,j,k]从1起始；mesh_offset从0起始；"
                     + mesh['formula'] + '；' + mesh['scope'])
        lines.append('首箱[1,1,1]→0；末箱'+str(mesh['last']['bin'])+'→'+str(mesh['last']['mesh_offset']))
    if facts.get('professional_acceptance_status'):lines.append(facts['professional_acceptance_status']['statement'])
    source = facts['source_convergence']
    if facts.get('material_source_summary'):lines.append(facts['material_source_summary'])
    lines += [facts['normalization'], source['statement']]
    if facts.get('zero_value_evidence'):lines.append(facts['zero_value_evidence']['statement'])
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
