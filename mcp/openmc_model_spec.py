"""Versioned declarative OpenMC input; no user code execution or inferred physics.

This is model-spec/v2. The v2 region is a typed tree and there is no string
region compatibility. Validation lives in the sibling ``model_validation``
package: Pydantic is used when importable, otherwise the stdlib fallback.
The builder and package can be copied together next to model-spec.json.
All coordinates are centimetres, energies eV and temperatures K.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
import shutil
import tempfile
import json
from pathlib import Path

try:
    from model_validation import (MODEL_SCHEMA, SCHEMA_VERSION, ValidationError,
                                  region_surface_ids, region_to_string, validate_spec, validation_report)
except ImportError:  # installed as mc_agent_tools.openmc_model_spec
    from .model_validation import (MODEL_SCHEMA, SCHEMA_VERSION, ValidationError,
                                   region_surface_ids, region_to_string, validate_spec, validation_report)


def model_assumptions(spec):
    """List defaults that the builder applies when optional fields are absent."""
    notes = []
    for material in spec.get('materials', []):
        if 'temperature' not in material:
            notes.append(f"材料 {material['id']} 未指定 temperature：使用 OpenMC 默认温度")
    for surface in spec.get('surfaces', []):
        if 'boundary' not in surface:
            notes.append(f"面 {surface['id']} 未指定 boundary：使用 transmission")
    settings = spec.get('settings', {})
    source = settings.get('source', {})
    if 'energy_ev' not in source:
        notes.append('源未指定 energy_ev：使用 OpenMC 默认源能谱')
    if 'strength' not in source:
        notes.append('源未指定 strength：使用 1.0')
    if 'seed' not in settings:
        notes.append('settings.seed 未指定：使用 1')
    if 'generations_per_batch' not in settings:
        notes.append('settings.generations_per_batch 未指定：使用 1')
    return notes


def declarative_integrity(directory=None):
    """Report whether a declarative model still matches its mc-case fingerprints."""
    directory = Path(directory) if directory is not None else Path.cwd()
    marker_path = directory / 'mc-case.json'
    if not marker_path.is_file():
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return {'declared_model': True, 'consistent': False,
                'reason': 'mc-case.json无法读取或解析：' + type(exc).__name__,
                'note': '无法确认声明式模型指纹；结果不能视为原声明模型产物。'}
    if marker.get('generation_method') != 'declarative_public_python_api':
        return None
    expected = marker.get('model_xml_sha256') or {}
    actual = {path.name: digest(path) for path in directory.glob('*.xml')}
    changed = sorted(name for name in expected if name in actual and actual[name] != expected[name])
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    spec_path = directory / 'model-spec.json'
    spec_changed = (not spec_path.is_file()
                    or digest(spec_path) != marker.get('model_spec_sha256'))
    consistent = bool(expected) and not changed and not missing and not extra and not spec_changed
    reasons = []
    if changed:
        reasons.append('已修改：' + ','.join(changed))
    if missing:
        reasons.append('已缺失：' + ','.join(missing))
    if extra:
        reasons.append('新增：' + ','.join(extra))
    if spec_changed:
        reasons.append('model-spec.json已修改或缺失')
    return {'declared_model': True, 'consistent': consistent,
            'changed_files': changed, 'missing_files': missing, 'extra_files': extra,
            'model_spec_changed': spec_changed,
            'reason': '；'.join(reasons) if reasons else None,
            'note': ('声明式模型XML与model-spec哈希一致。' if consistent else
                     '声明式模型已漂移；本次提交仍可执行，但结果不能视为该声明的原始产物；'
                     '建议从model-spec重建，或用openmc_import_model重新导入为原生模型。')}


def build(spec):
    """Create inputs in cwd using public OpenMC APIs, then parse the actual output."""
    report = validation_report(spec)
    import openmc
    materials = {}
    for s in spec['materials']:
        m = openmc.Material(material_id=s['id'], name=s['name'])
        m.set_density(s['density_unit'], s['density'])
        for n in s['nuclides']:
            m.add_nuclide(n['name'], n['fraction'], s['percent_type'])
        for sab in s.get('sab', []):
            m.add_s_alpha_beta(sab)
        if 'temperature' in s:
            m.temperature = s['temperature']
        if 'volume' in s:
            m.volume = s['volume']
        m.depletable = s.get('depletable', False)
        materials[m.id] = m
    constructors = {
        'x-plane': (openmc.XPlane, {'x0'}), 'y-plane': (openmc.YPlane, {'y0'}),
        'z-plane': (openmc.ZPlane, {'z0'}), 'x-cylinder': (openmc.XCylinder, {'y0', 'z0', 'r'}),
        'y-cylinder': (openmc.YCylinder, {'x0', 'z0', 'r'}),
        'z-cylinder': (openmc.ZCylinder, {'x0', 'y0', 'r'}),
        'sphere': (openmc.Sphere, {'x0', 'y0', 'z0', 'r'}),
    }
    surfaces = {}
    for s in spec['surfaces']:
        cls, allowed = constructors[s['type']]
        values = s['parameters']
        if set(values) - allowed:
            raise ValueError('surface参数与类型不匹配')
        if 'r' in allowed and 'r' not in values:
            raise ValueError('曲面必须显式提供r')
        surfaces[s['id']] = cls(surface_id=s['id'], boundary_type=s.get('boundary', 'transmission'), **values)
    universes = {i: openmc.Universe(universe_id=i) for i in {c['universe'] for c in spec['cells']}}
    lattices = {}
    for s in spec.get('lattices', []):
        lat = openmc.RectLattice(lattice_id=s['id'], name=s.get('name', ''))
        lat.lower_left = s['lower_left']
        lat.pitch = s['pitch']
        lat.universes = [[universes[i] for i in row] for row in s['universes']]
        if 'outer' in s and s['outer'] is not None:
            lat.outer = universes[s['outer']]
        lattices[s['id']] = lat
    for s in spec['cells']:
        f = s['fill']
        fill = None if f['type'] == 'void' else {'material': materials, 'universe': universes, 'lattice': lattices}[f['type']][f['id']]
        cell = openmc.Cell(cell_id=s['id'], name=s.get('name', ''), fill=fill,
                           region=openmc.Region.from_expression(region_to_string(s['region']), surfaces))
        universes[s['universe']].add_cell(cell)
    geometry = openmc.Geometry(universes[spec['root_universe']])
    settings = openmc.Settings()
    s = spec['settings']
    settings.run_mode = s['run_mode']
    settings.particles = s['particles']
    settings.batches = s['batches']
    settings.inactive = s['inactive']
    settings.seed = s.get('seed', 1)
    settings.generations_per_batch = s.get('generations_per_batch', 1)
    src = s['source']
    space = (openmc.stats.Point(src['point']) if src['type'] == 'point'
             else openmc.stats.Box(src['lower_left'], src['upper_right']))
    energy = {'energy': openmc.stats.Discrete([src['energy_ev']], [1.0])} if 'energy_ev' in src else {}
    settings.source = openmc.IndependentSource(space=space, strength=src.get('strength', 1.0), **energy)
    tallies = openmc.Tallies()
    next_filter_id = 1
    for s in spec.get('tallies', []):
        tally = openmc.Tally(tally_id=s['id'], name=s.get('name', ''))
        tally.scores = s['scores']
        if 'nuclides' in s:
            tally.nuclides = s['nuclides']
        filters = []
        for f in s.get('filters', []):
            if f['type'] == 'energy':
                filters.append(openmc.EnergyFilter(f['bins'], filter_id=next_filter_id))
            elif f['type'] == 'material':
                filters.append(openmc.MaterialFilter(f['bins'], filter_id=next_filter_id))
            else:
                mesh = openmc.RegularMesh(mesh_id=s['id'])
                mesh.dimension = f['dimension']
                mesh.lower_left = f['lower_left']
                mesh.upper_right = f['upper_right']
                filters.append(openmc.MeshFilter(mesh, filter_id=next_filter_id))
            next_filter_id += 1
        tally.filters = filters
        tallies.append(tally)
    model = openmc.Model(geometry, openmc.Materials(list(materials.values())), settings, tallies)
    model.export_to_xml()
    parsed = openmc.Model.from_xml()
    save_json(Path('model-spec.json'), spec)
    assumptions = model_assumptions(spec)
    marker = model_metadata(Path('.'))
    marker['assumptions'] = assumptions
    marker['validation'] = report
    save_json(Path('mc-case.json'), marker)
    return {'model_spec_sha256': marker['model_spec_sha256'],
            'model_xml_sha256': marker['model_xml_sha256'],
            'builder_source_sha256': marker['builder_source_sha256'],
            'openmc_version': openmc.__version__, 'materials': len(parsed.materials),
            'cells': len(parsed.geometry.get_all_cells()),
            'lattices': len(parsed.geometry.get_all_lattices()),
            'tallies': len(parsed.tallies), 'api_parsed': True,
            'assumptions': assumptions, 'validation': report,
            'files': [p.name for p in Path('.').glob('*.xml')]}


def save_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_metadata(directory):
    import openmc
    return {'data_kind': 'user_supplied_unverified',
            'generation_method': 'declarative_public_python_api',
            'openmc_version': openmc.__version__, 'schema_version': SCHEMA_VERSION,
            'builder_source_sha256': digest(Path(__file__).resolve()),
            'model_spec_sha256': digest(directory / 'model-spec.json'),
            'model_xml_sha256': {p.name: digest(p) for p in directory.glob('*.xml')},
            'fine_tuned_model_used': False, 'professional_acceptance': False,
            'note': '用户/宿主显式参数经OpenMC Python API生成；未专业审定。'}


def checked_declarative_spec():
    """Read/hash once per preflight; native models return None."""
    directory = Path.cwd()
    marker_path = directory / 'mc-case.json'
    if not marker_path.is_file():
        return None
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    if marker.get('generation_method') != 'declarative_public_python_api':
        return None
    expected = marker.get('model_xml_sha256')
    actual = {p.name: digest(p) for p in directory.glob('*.xml')}
    spec_path = directory / 'model-spec.json'
    if (not expected or actual != expected or not spec_path.is_file()
            or digest(spec_path) != marker.get('model_spec_sha256')):
        raise ValueError('声明式模型XML或model-spec已漂移，未覆盖；请重新导入为原生模型或从新spec重建')
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    validate_spec(spec)
    return spec


def mapped_declarative_spec(baseline, parameters):
    """Pure parameter projection used identically by preflight and generation."""
    spec = deepcopy(baseline)
    materials = {m['id']: m for m in spec['materials']}
    surfaces = {s['id']: s for s in spec['surfaces']}
    for key, value in parameters.items():
        parts = key.split('.')
        if parts[0] == 'materials':
            materials[int(parts[1])][parts[2]] = value
        elif parts[:2] == ['geometry', 'surfaces']:
            surfaces[int(parts[2])]['parameters'][parts[3]] = value
        elif parts[:2] == ['settings', 'source']:
            if parts[2:] != ['0', 'strength']:
                raise ValueError('model-spec只支持一个源的strength修改')
            spec['settings']['source']['strength'] = value
        else:
            spec['settings'][parts[1]] = value
    validate_spec(spec)
    return spec


def _copy_validation_package(staging):
    source = Path(__file__).resolve().parent / 'model_validation'
    if not source.is_dir():
        raise ValueError('缺少 model_validation 包，无法生成可重建算例')
    shutil.copytree(source, staging / 'model_validation', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def rebuild_parameters(parameters):
    """Keep declarative source and native inputs in sync without running saved code."""
    directory = Path.cwd()
    baseline = checked_declarative_spec()
    if baseline is None:
        raise ValueError('不是声明式模型')
    spec = mapped_declarative_spec(baseline, parameters)
    marker = json.loads((directory / 'mc-case.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='.spec-update-', dir=directory) as tmp:
        staging = Path(tmp)
        try:
            os.chdir(staging)
            build(spec)
        finally:
            os.chdir(directory)
        shutil.copyfile(Path(__file__).resolve(), staging / 'build_model.py')
        _copy_validation_package(staging)
        generated = json.loads((staging / 'mc-case.json').read_text(encoding='utf-8'))
        marker.update(generated)
        save_json(staging / 'mc-case.json', marker)
        for name in [*generated['model_xml_sha256'], 'model-spec.json', 'build_model.py', 'mc-case.json']:
            os.replace(staging / name, directory / name)
        target_package = directory / 'model_validation'
        if target_package.exists():
            shutil.rmtree(target_package)
        shutil.copytree(staging / 'model_validation', target_package)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Validate or build an OpenMC model-spec/v2.')
    parser.add_argument('--validate', action='store_true', help='只校验 model-spec.json，不导入 OpenMC')
    parser.add_argument('--backend', choices=['auto', 'pydantic', 'fallback'], default='auto',
                        help='校验后端；auto优先Pydantic，缺失时使用标准库fallback')
    args = parser.parse_args()
    spec = json.loads(Path('model-spec.json').read_text(encoding='utf-8'))
    if args.validate:
        try:
            report = validation_report(spec, backend=args.backend)
        except ValidationError as exc:
            print(json.dumps({'valid': False, 'error': str(exc)}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(json.dumps({'valid': True, 'schema_version': spec['schema_version'],
                          'backend': report['backend'], 'assumptions': model_assumptions(spec),
                          'overrides': report['overrides']}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(build(spec), ensure_ascii=False))
