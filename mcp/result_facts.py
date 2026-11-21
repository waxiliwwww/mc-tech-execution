"""Bounded, directly quotable facts from submitted inputs and selected results."""
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET


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
               'mean': mean, 'std_dev': std}
        if series.get('mesh_indices') is not None:
            row['mesh_bin'] = [v + 1 for v in series['mesh_indices'][index]]
        rows.append(row)
    return rows


def value_table(rows):
    lines = ['| 选择序号 | 网格箱[i,j,k]（1起始） | score | nuclide | mean | std_dev |',
             '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        def cell(value):
            return str(value).replace('|', '\\|').replace('\n', ' ')
        lines.append('| ' + ' | '.join(cell(row.get(key, '不适用')) for key in
                     ('selected_index', 'mesh_bin', 'score', 'nuclide', 'mean', 'std_dev')) + ' |')
    return '\n'.join(lines)
