"""Public OpenMC parameter adapters. Paths are discovered from an explicit allow list."""
from __future__ import annotations

import math

SETTINGS = {'particles': ('integer', 1, 'particles/generation'),
            'batches': ('integer', 2, 'batches'), 'inactive': ('integer', 0, 'batches'),
            'generations_per_batch': ('integer', 1, 'generations/batch'),
            'seed': ('integer', 1, 'random seed')}
SURFACES = {'XPlane': ('x0',), 'YPlane': ('y0',), 'ZPlane': ('z0',),
            'Plane': ('a', 'b', 'c', 'd'), 'Sphere': ('x0', 'y0', 'z0', 'r'),
            'XCylinder': ('y0', 'z0', 'r'), 'YCylinder': ('x0', 'z0', 'r'),
            'ZCylinder': ('x0', 'y0', 'r'), 'XCone': ('x0', 'y0', 'z0', 'r2'),
            'YCone': ('x0', 'y0', 'z0', 'r2'), 'ZCone': ('x0', 'y0', 'z0', 'r2')}


def load_model():
    import openmc
    from pathlib import Path
    return openmc.Model.from_model_xml('model.xml') if Path('model.xml').exists() else openmc.Model.from_xml()


def describe(model):
    items = []
    for field, (kind, minimum, unit) in SETTINGS.items():
        items.append({'name': 'settings.' + field, 'type': kind, 'minimum': minimum,
                      'current': getattr(model.settings, field), 'unit': unit,
                      'category': 'statistical_setting'})
    for material in model.materials:
        if material.density_units != 'sum' and material.density is not None:
            items.append({'name': f'materials.{material.id}.density', 'type': 'number',
                          'exclusive_minimum': 0, 'current': material.density,
                          'unit': material.density_units, 'category': 'material',
                          'note': ('macro是无量纲宏观截面倍率，1.0表示原库的1倍；不是g/cm³，不能直接填质量密度。'
                                   if material.density_units == 'macro' else '保持原密度单位；不自动换算单位。')})
        items.append({'name': f'materials.{material.id}.temperature', 'type': 'number',
                      'exclusive_minimum': 0, 'current': material.temperature, 'unit': 'K',
                      'category': 'material', 'note': 'current=null表示材料未显式设置，可能继承cell/settings/properties；不等于有效温度为0或缺数据。实际温度还依赖适用核数据。'})
    for surface in model.geometry.get_all_surfaces().values():
        for field in SURFACES.get(type(surface).__name__, ()):
            items.append({'name': f'geometry.surfaces.{surface.id}.{field}', 'type': 'number',
                          'current': float(getattr(surface, field)),
                          'unit': 'cm' if field in ('x0', 'y0', 'z0', 'r') else 'OpenMC surface coefficient',
                          'category': 'geometry',
                          **({'exclusive_minimum': 0} if field in ('r', 'r2') else {})})
    for index, source in enumerate(model.settings.source or []):
        items.append({'name': f'settings.source.{index}.strength', 'type': 'number',
                      'current': source.strength, 'exclusive_minimum': 0, 'unit': 'relative strength',
                      'category': 'source'})
    return items


def checked(model, parameters):
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError('parameters须为非空参数对象；先调用openmc_list_parameters')
    specs = {item['name']: item for item in describe(model)}
    for name, value in parameters.items():
        if name not in specs:
            raise ValueError('不支持的参数：' + name + '；先调用openmc_list_parameters')
        spec = specs[name]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(name + '须为有限数值')
        if spec['type'] == 'integer' and type(value) is not int:
            raise ValueError(name + '须为整数')
        if ('minimum' in spec and value < spec['minimum']) or ('exclusive_minimum' in spec and value <= spec['exclusive_minimum']):
            raise ValueError(name + '超出支持范围；当前值=' + str(spec.get('current')) + '；要求=' + str({k: v for k,v in spec.items() if k in ('minimum','maximum','exclusive_minimum','exclusive_maximum','type','unit')}))
    batches = parameters.get('settings.batches', model.settings.batches)
    inactive = parameters.get('settings.inactive', model.settings.inactive or 0)
    if batches is not None and batches - inactive < 2:
        raise ValueError('batches-inactive至少为2；这是工程统计样例约束，不是专业准确率判据')
    return specs


def apply(model, parameters):
    specs = checked(model, parameters)
    materials = {m.id: m for m in model.materials}
    surfaces = model.geometry.get_all_surfaces()
    for name, value in parameters.items():
        pieces = name.split('.')
        # Every branch is restricted to the previously discovered public properties.
        if pieces[0] == 'materials':
            material = materials[int(pieces[1])]
            if pieces[2] == 'density':material.set_density(material.density_units, value)
            else:material.temperature = value
        elif pieces[:2] == ['geometry', 'surfaces']:
            setattr(surfaces[int(pieces[2])], pieces[3], value)
        elif pieces[:2] == ['settings', 'source']:
            model.settings.source[int(pieces[2])].strength = value
        else:
            setattr(model.settings, pieces[1], value)
    return [{'name': name, 'before': specs[name]['current'], 'after': value,
             'unit': specs[name]['unit'], 'category': specs[name]['category']}
            for name, value in parameters.items() if specs[name]['current'] != value]


def write_parameters(model, parameters):
    """Patch only requested XML fields after public-property validation.

    A full Model XML round trip in OpenMC 0.16 omits macroscopic material entries.
    Preserve all other original XML, including fields not represented by this adapter.
    """
    from pathlib import Path
    from lxml import etree as ET
    import json
    marker=Path('mc-case.json')
    if marker.is_file() and json.loads(marker.read_text()).get('generation_method')=='declarative_public_python_api':
        from openmc_model_spec import rebuild_parameters
        rebuild_parameters(parameters)
        return
    integrated = Path('model.xml').exists()
    trees = {}
    def section(name):
        filename = 'model.xml' if integrated else name + '.xml'
        if filename not in trees:trees[filename] = ET.parse(filename)
        return trees[filename].getroot().find(name) if integrated else trees[filename].getroot()
    def child(parent, name):
        result = parent.find(name)
        return result if result is not None else ET.SubElement(parent, name)
    surfaces = model.geometry.get_all_surfaces()
    for name, value in parameters.items():
        pieces = name.split('.')
        if pieces[0] == 'materials':
            material = section('materials').find(f"material[@id='{pieces[1]}']")
            if pieces[2] == 'density':child(material, 'density').set('value', str(value))
            else:material.set('temperature', str(value))
        elif pieces[:2] == ['geometry', 'surfaces']:
            surface = section('geometry').find(f"surface[@id='{pieces[2]}']")
            surface.set('coeffs', surfaces[int(pieces[2])].to_xml_element().get('coeffs'))
        elif pieces[:2] == ['settings', 'source']:
            section('settings').findall('source')[int(pieces[2])].set('strength', str(value))
        else:child(section('settings'), pieces[1]).text = str(value)
    for filename, tree in trees.items():tree.write(filename, encoding='utf-8', xml_declaration=True)
