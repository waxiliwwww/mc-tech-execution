"""Canonical declarative model contract.

This module is deliberately independent of Pydantic and OpenMC. It owns the
shape-independent rules that every backend must apply: region tree conversion,
semantic cross-field checks, and expert-override tagging. The input is the
current schema; callers do not select or declare a schema version.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import NamedTuple

MAX_TALLY_VALUES = 1_000_000
OVERRIDE_CODES = ('outer_boundary_transmission',)
MAX_REGION_DEPTH = 80
UNVERIFIED = ('geometry_coverage', 'physical_correctness', 'convergence')

SCHEMA_PATH = Path(__file__).with_name('schema.json')
if not SCHEMA_PATH.is_file():
    raise RuntimeError('缺少 model_validation/schema.json；无法校验模型输入')
MODEL_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


class ValidationError(ValueError):
    """One stable, user-facing validation failure."""


class HalfSpace(NamedTuple):
    surface: int
    side: str


class Intersection(NamedTuple):
    nodes: tuple


class Union(NamedTuple):
    nodes: tuple


class Complement(NamedTuple):
    node: object


def _region_error(message):
    raise ValidationError('region：' + message)


def check_region_depth(spec):
    """Reject absurdly nested region trees before a recursive backend parses them."""
    if not isinstance(spec, dict) or not isinstance(spec.get('cells'), list):
        return
    for cell in spec['cells']:
        root = cell.get('region') if isinstance(cell, dict) else None
        if not isinstance(root, dict):
            continue
        stack = [(root, 1)]
        while stack:
            node, depth = stack.pop()
            if depth > MAX_REGION_DEPTH:
                raise ValidationError('region嵌套过深；最多支持80层')
            if not isinstance(node, dict):
                continue
            if isinstance(node.get('items'), list):
                stack.extend((item, depth + 1) for item in node['items'])
            if isinstance(node.get('item'), dict):
                stack.append((node['item'], depth + 1))


def region_from_dict(data):
    if not isinstance(data, dict):
        _region_error('必须是对象')
    op = data.get('op')
    if op == 'halfspace':
        return HalfSpace(data['surface'], data['side'])
    if op in ('intersection', 'union'):
        values = [region_from_dict(item) for item in data['items']]
        return Intersection(tuple(values)) if op == 'intersection' else Union(tuple(values))
    if op == 'complement':
        return Complement(region_from_dict(data['item']))
    _region_error('不支持的 op：' + str(op))


def region_surface_ids(data):
    stack = [region_from_dict(data)]
    ids = []
    while stack:
        node = stack.pop()
        if isinstance(node, HalfSpace):
            ids.append(node.surface)
        elif isinstance(node, (Intersection, Union)):
            stack.extend(reversed(node.nodes))
        elif isinstance(node, Complement):
            stack.append(node.node)
    return ids


def _region_text(node, parent=0):
    if isinstance(node, HalfSpace):
        text = f'-{node.surface}' if node.side == 'negative' else str(node.surface)
        precedence = 4
    elif isinstance(node, Complement):
        text = '~' + _region_text(node.node, 3)
        precedence = 3
    elif isinstance(node, Intersection):
        text = ' '.join(_region_text(item, 2) for item in node.nodes)
        precedence = 2
    elif isinstance(node, Union):
        text = ' | '.join(_region_text(item, 1) for item in node.nodes)
        precedence = 1
    else:
        _region_error('内部节点类型错误')
    return f'({text})' if precedence < parent else text


def region_to_string(data):
    """Serialize a typed region tree to OpenMC's expression grammar."""
    return _region_text(region_from_dict(data))


def _ids(spec):
    ids = {}
    for kind in ('materials', 'surfaces', 'cells', 'lattices', 'tallies'):
        entries = spec.get(kind, [])
        values = {entry['id']: entry for entry in entries}
        if len(values) != len(entries):
            raise ValidationError(kind + ' 编号重复')
        ids[kind] = values
    return ids


def _overrides(spec):
    values = spec.get('validation', {}).get('overrides', [])
    codes = [item['code'] for item in values]
    if len(codes) != len(set(codes)):
        raise ValidationError('validation.overrides 同一 code 不能重复')
    for item in values:
        if item['code'] not in OVERRIDE_CODES:
            raise ValidationError('validation.overrides 不支持的 code：' + item['code'])
    return values


def validate_semantics(spec, backend='fallback'):
    """Validate cross-field invariants after shape validation.

    A declared override is only a record of expert intent. It suppresses the
    named hard condition, never syntax/type/reference errors, and the applied
    override is returned so jobs and reports can carry a visible tag.
    """
    ids = _ids(spec)
    for material in spec['materials']:
        names = [n['name'] for n in material['nuclides']]
        if len(set(names)) != len(names):
            raise ValidationError('材料核素重复')

    universes = {cell['universe'] for cell in spec['cells']}
    if spec['root_universe'] not in universes:
        raise ValidationError('根宇宙不存在')
    if universes & ids['lattices'].keys():
        raise ValidationError('宇宙和格架编号不可冲突')
    graph = {u: [] for u in universes}

    def edge(parent, kind, identifier):
        pool = (ids['materials'] if kind == 'material'
                else universes if kind == 'universe' else ids['lattices'])
        if identifier not in pool:
            raise ValidationError(f'{kind} 引用不存在：{identifier}')
        if kind == 'universe':
            graph[parent].append(identifier)
        if kind == 'lattice':
            lattice = ids['lattices'][identifier]
            rows = lattice['universes']
            if len({len(row) for row in rows}) != 1:
                raise ValidationError('格架每行长度必须一致')
            targets = [value for row in rows for value in row]
            if 'outer' in lattice and lattice['outer'] is not None:
                targets.append(lattice['outer'])
            for target in targets:
                edge(parent, 'universe', target)

    cell_region_ids = {}
    for cell in spec['cells']:
        try:
            surface_ids = region_surface_ids(cell['region'])
        except ValidationError as exc:
            raise ValidationError(f'cells[{cell["id"]}].{exc}') from exc
        cell_region_ids[cell['id']] = surface_ids
        for surface_id in surface_ids:
            if surface_id not in ids['surfaces']:
                raise ValidationError(f'region面引用不存在：{surface_id}')
        fill = cell['fill']
        if fill['type'] == 'void':
            if fill.get('id') is not None:
                raise ValidationError('void不能带id')
        else:
            edge(cell['universe'], fill['type'], fill['id'])

    declared = _overrides(spec)
    applied = []
    applied_codes = set()
    warnings = []

    def apply_override(code):
        for item in declared:
            if item['code'] == code:
                if code not in applied_codes:
                    applied.append({'code': code, 'reason': item['reason'], 'scope': item['scope']})
                    applied_codes.add(code)
                return True
        return False

    root_uses = {}
    for cell in spec['cells']:
        if cell['universe'] == spec['root_universe']:
            for surface_id in set(cell_region_ids[cell['id']]):
                root_uses[surface_id] = root_uses.get(surface_id, 0) + 1
    for surface_id, uses in root_uses.items():
        surface = ids['surfaces'][surface_id]
        if uses == 1 and surface.get('boundary', 'transmission') == 'transmission':
            if apply_override('outer_boundary_transmission'):
                continue
            raise ValidationError(
                f'根宇宙外边界面 {surface_id} 不能是 transmission；'
                '请显式设 vacuum、reflective 或 white，避免无界几何或粒子逸出')

    used_lattices = {cell['fill']['id'] for cell in spec['cells'] if cell['fill']['type'] == 'lattice'}
    if used_lattices != set(ids['lattices']):
        raise ValidationError('存在未引用格架，拒绝静默忽略')

    done = set()
    visiting = set()

    def visit(universe, depth=0):
        if depth > 80:
            raise ValidationError('宇宙引用深度超限')
        if universe in visiting:
            raise ValidationError('宇宙/格架循环引用')
        if universe in done:
            return
        visiting.add(universe)
        for child in graph[universe]:
            visit(child, depth + 1)
        visiting.remove(universe)
        done.add(universe)

    visit(spec['root_universe'])
    if done != universes:
        raise ValidationError('存在根模型不可达的宇宙，拒绝静默忽略')

    settings = spec['settings']
    if settings['batches'] - settings['inactive'] < 2:
        raise ValidationError('至少需要两个活跃批次')
    if settings['run_mode'] == 'fixed source' and settings['inactive'] != 0:
        raise ValidationError('固定源inactive必须为0')
    source = settings['source']
    if source['type'] == 'box':
        if any(a >= b for a, b in zip(source['lower_left'], source['upper_right'])):
            raise ValidationError('盒源下界必须小于上界')

    total_tally_values = 0
    for tally in spec.get('tallies', []):
        tally_values = len(tally['scores']) * len(tally.get('nuclides', ['total']))
        kinds = [item['type'] for item in tally.get('filters', [])]
        if len(kinds) != len(set(kinds)):
            raise ValidationError('同一tally过滤类型不可重复')
        for item in tally.get('filters', []):
            if item['type'] == 'mesh':
                if any(value < 1 for value in item['dimension']):
                    raise ValidationError('网格每维必须为正整数')
                tally_values *= math.prod(item['dimension'])
                if any(a >= b for a, b in zip(item['lower_left'], item['upper_right'])):
                    raise ValidationError('网格下界必须小于上界')
            else:
                bins = item['bins']
                tally_values *= len(bins) - 1 if item['type'] == 'energy' else len(bins)
                if item['type'] == 'energy' and (
                        len(bins) < 2 or bins[0] < 0 or any(a >= b for a, b in zip(bins, bins[1:]))):
                    raise ValidationError('能量边界须非负且严格递增')
                if item['type'] == 'material' and any(
                        type(value) is not int or value not in ids['materials'] for value in bins):
                    raise ValidationError('材料过滤引用不存在')
        total_tally_values += tally_values
        if total_tally_values > MAX_TALLY_VALUES:
            raise ValidationError('组合计分值数量超过建模接口上限1000000；请缩减过滤/核素/score组合')

    for item in declared:
        if item['code'] not in applied_codes:
            warnings.append('覆盖未触发：' + item['code'])
    return {
        'backend': backend,
        'overrides': applied,
        'warnings': warnings,
        'unverified': list(UNVERIFIED),
    }
