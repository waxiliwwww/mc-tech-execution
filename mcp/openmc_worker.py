"""Small process boundary around public OpenMC 0.16 APIs, also usable in a container."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import sys


def numpy_scalar(value):
    import numpy as np
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError('Unsupported JSON result type: ' + type(value).__name__)


def execute(action: str, params: dict) -> dict:
    import openmc
    import numpy as np

    if openmc.__version__ != params['_expected_version']:
        raise ValueError('OpenMC Python API版本不符：' + openmc.__version__ + '；需要' + params['_expected_version'])

    if action == 'probe':
        return {'python_api': True, 'api_version': openmc.__version__}
    if action in ('plot-result', 'plot-series', 'render-report', 'select-series'):
        from openmc_visuals import draw, plot_result, render_docx, selected_series
        if action == 'plot-result':return plot_result(params)
        if action == 'plot-series':return draw(params['series'], Path(params['filename']), params)
        if action == 'render-report':return render_docx(params)
        with openmc.StatePoint(params['statepoint'], autolink=False) as point:
            return selected_series(point, params)
    if action in ('parameters', 'check-parameters', 'update-parameters'):
        from openmc_parameters import apply, checked, describe, load_model, write_parameters
        model = load_model()
        if action == 'parameters':
            return {'parameters': describe(model), 'note': '仅列当前适配器支持的公开属性；sum密度及未适配表面不提供修改入口。'}
        if action == 'check-parameters':
            for values in params['combinations']:checked(model, values)
            return {'valid': True, 'combinations': len(params['combinations']), 'parameters': describe(model)}
        changes = apply(model, params['parameters'])
        write_parameters(model, params['parameters'])
        marker = Path('mc-case.json')
        info = json.loads(marker.read_text()) if marker.exists() else {'data_kind': 'user_supplied_unverified'}
        info.update(parameter_changes=changes, professional_acceptance=False)
        marker.write_text(json.dumps(info, ensure_ascii=False, indent=2, default=numpy_scalar), encoding='utf-8')
        return {'changes': changes, 'professional_acceptance': False}
    if action == 'create':
        template = params['template']
        if template == 'synthetic-mgxs':
            groups = openmc.mgxs.EnergyGroups([0.0, 20.0e6])
            xs = openmc.XSdata('synthetic_material', groups)
            xs.order = 0
            xs.set_total([1.0])
            xs.set_absorption([0.3])
            xs.set_scatter_matrix(np.array([[[0.7]]]))
            xs.set_fission([0.2])
            xs.set_nu_fission([0.5])
            xs.set_chi([1.0])
            library = openmc.MGXSLibrary(groups)
            library.add_xsdata(xs)
            library.export_to_hdf5('synthetic-mgxs.h5')
            material = openmc.Material(name='Synthetic one-group demonstration')
            material.set_density('macro', 1.0)
            material.add_macroscopic('synthetic_material')
            materials = openmc.Materials([material])
            materials.cross_sections = 'synthetic-mgxs.h5'
            box = openmc.model.RectangularParallelepiped(-10, 10, -10, 10, -10, 10,
                                                       boundary_type='reflective')
            geometry = openmc.Geometry([openmc.Cell(fill=material, region=-box)])
            settings = openmc.Settings()
            settings.energy_mode = 'multi-group'
            settings.source = openmc.IndependentSource(space=openmc.stats.Point())
            tally = openmc.Tally(name='demonstration flux and reaction rates')
            tally.scores = ['flux', 'fission', 'absorption']
            model = openmc.Model(geometry, materials, settings, openmc.Tallies([tally]))
            data_kind = 'synthetic_mgxs'
            note = '合成单群截面，仅用于接口、运行及产物解析验证，不代表真实材料或专业计算结论。'
        elif template == 'pwr-pin-cell':
            model = openmc.examples.pwr_pin_cell()
            data_kind = 'public_example_requires_nuclear_data'
            note = 'OpenMC官方PWR pin-cell示例；运行须配置相应真实核数据，不是甲方指定基准。'
        elif template == 'pincell-multigroup':
            import hashlib
            import runpy
            directory = Path(__file__).resolve().parents[1] / 'vendor/openmc-examples/0.16.0'
            source = directory / 'pincell_multigroup.py'
            metadata = json.loads((directory / 'manifest.json').read_text())['files'][0]
            if hashlib.sha256(source.read_bytes()).hexdigest() != metadata['sha256']:
                raise ValueError('官方例题源码与清单不一致，拒绝执行')
            # Fixed, bundled upstream code; the caller cannot supply a script path.
            namespace = runpy.run_path(str(source))
            model = openmc.Model(namespace['geometry'], namespace['materials_file'],
                                 namespace['settings'], namespace['tallies'])
            plot = openmc.Plot(); plot.width = (1.26, 1.26); plot.pixels = (400, 400); plot.color_by = 'material'
            model.plots = openmc.Plots([plot])
            data_kind = 'public_c5g7_multigroup_example'
            note = 'OpenMC官方多群pin-cell，使用源码自带C5G7多群数据；本次运行设置由调用参数指定，未作甲方基准或专业准确率验证。'
        else:
            raise ValueError('Unsupported template')
        model.settings.particles = params['particles']
        model.settings.batches = params['batches']
        model.settings.inactive = params['inactive']
        model.settings.seed = 1
        if not model.plots:
            plot = openmc.Plot()
            plot.width = (20.0, 20.0)
            plot.pixels = (400, 400)
            plot.color_by = 'material'
            model.plots = openmc.Plots([plot])
        model.export_to_xml()
        model.plots.export_to_xml()
        if template in ('synthetic-mgxs', 'pincell-multigroup'):
            # Keep the generated engineering example movable with its own data file.
            from lxml import etree as ET
            tree = ET.parse('materials.xml')
            tree.getroot().find('cross_sections').text = 'synthetic-mgxs.h5' if template == 'synthetic-mgxs' else 'mgxs.h5'
            tree.write('materials.xml', encoding='utf-8', xml_declaration=True)
        marker = {'template': template, 'data_kind': data_kind, 'openmc_version': openmc.__version__,
                  'professional_acceptance': False, 'note': note}
        if template == 'pincell-multigroup':
            marker.update(source_url=metadata['source_url'], source_sha256=metadata['sha256'],
                          runtime_overrides={k: params[k] for k in ('particles', 'batches', 'inactive')})
        Path('mc-case.json').write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding='utf-8')
        return {**marker, 'files': sorted(p.name for p in Path('.').iterdir() if p.is_file())}
    if action == 'validate':
        model = openmc.Model.from_model_xml('model.xml') if Path('model.xml').exists() else openmc.Model.from_xml()
        return {'api_parsed': True, 'cells': len(model.geometry.get_all_cells()),
                'materials': len(model.materials), 'tallies': len(model.tallies),
                'note': '公开API能够读取模型；未进行几何重叠、核数据完备性或物理正确性判定。'}
    if action == 'results':
        limit = params.get('max_values', 1000)
        warnings = []
        def values(array, cap=limit):
            flat = np.asarray(array).flatten()
            result = []
            for number in flat[:cap]:
                if np.isfinite(number):
                    result.append(float(number))
                else:
                    result.append(None)
                    warning = '统计量包含非有限值；保留为空，未替换为0；需检查统计条件。'
                    if warning not in warnings:
                        warnings.append(warning)
            return result
        with openmc.StatePoint(params['statepoint'], autolink=False) as point:
            keff = point.keff if point.run_mode == 'eigenvalue' else None
            tallies = []
            for tally in point.tallies.values():
                tallies.append({'id': tally.id, 'name': tally.name, 'scores': tally.scores,
                                'nuclides': tally.nuclides, 'estimator': tally.estimator,
                                'filters': [{'type': type(f).__name__, 'description': str(f)[:2000]} for f in tally.filters],
                                'shape': list(tally.mean.shape), 'mean': values(tally.mean),
                                'std_dev': values(tally.std_dev), 'values_truncated': tally.mean.size > limit,
                                'normalization': 'OpenMC原始计分归一化；未换算为工程功率或实际源强。'})
            keff_values = values([keff.nominal_value, keff.std_dev], cap=2) if keff is not None else None
            return {'openmc_version': list(point.version), 'run_mode': point.run_mode,
                    'particles': point.n_particles, 'realizations': point.n_realizations,
                    'keff': None if keff is None else {'mean': keff_values[0], 'std_dev': keff_values[1]},
                    'tallies': tallies, 'warnings': warnings}
    if action == 'inspect-h5m':
        universe = openmc.DAGMCUniverse(params['filename'])
        box = universe.bounding_box
        return {'readable': True,
                'bounding_box': {'lower_left': [float(v) for v in box.lower_left],
                                 'upper_right': [float(v) for v in box.upper_right]},
                'note': 'DAGMC版OpenMC API能读取该h5m并给出包围盒；未作输运计算或几何质量判定。'}
    if action == 'build-dagmc-model':
        import xml.etree.ElementTree as ET
        universe = openmc.DAGMCUniverse('dagmc.h5m', universe_id=1, auto_geom_ids=True)
        materials = openmc.Materials()
        warnings = []
        for index, spec in enumerate(params['materials'], start=1):
            material = openmc.Material(material_id=1000 + index, name=spec['name'])
            material.set_density(spec['units'], spec['density'])
            for item in spec.get('nuclides') or []:
                material.add_nuclide(item['name'], item['fraction'],
                                     percent_type=item.get('percent_type', 'wo'))
            if not spec.get('nuclides'):
                warnings.append('材料' + spec['name'] + '未给核素组成；OpenMC输运前须补全。')
            materials.append(material)
        geometry = openmc.Geometry(root=universe)
        settings = openmc.Settings()
        settings.run_mode = 'eigenvalue'
        settings.particles = params['particles']
        settings.batches = params['batches']
        settings.inactive = params['inactive']
        settings.seed = 1 if params.get('seed') is None else params['seed']
        point = tuple(params.get('source_point') or [0.0, 0.0, 0.0])
        settings.source = openmc.IndependentSource(space=openmc.stats.Point(point))
        model = openmc.Model(geometry, materials, settings)
        model.export_to_model_xml()
        # 模型要随算例搬移：把DAGMC绝对路径改为相对文件名（与create_model改cross_sections同款）。
        tree = ET.parse('model.xml')
        for node in tree.getroot().iter('dagmc_universe'):
            node.set('filename', 'dagmc.h5m')
        tree.write('model.xml', encoding='unicode', xml_declaration=True)
        reparsed = openmc.Model.from_model_xml('model.xml')
        return {'files': ['model.xml'], 'dagmc_filename': 'dagmc.h5m',
                'materials': [spec['name'] for spec in params['materials']],
                'settings': {'particles': params['particles'], 'batches': params['batches'],
                             'inactive': params['inactive']},
                'source_point': list(point),
                'engine_parse': {'ok': True, 'materials': len(reparsed.materials)},
                'warnings': warnings,
                'note': '模型XML由公开API组装并回读验证；DAGMC材料指派、网格质量与源位置是否在模型内需业务确认。'}
    if action == 'add-tally':
        from pathlib import Path as _Path
        tallies_path = _Path('tallies.xml')
        tallies = openmc.Tallies.from_xml(str(tallies_path)) if tallies_path.is_file() else openmc.Tallies()
        used = {int(tally.id) for tally in tallies}
        identifier = params.get('tally_id')
        if identifier is None:
            identifier = max(used, default=0) + 1
        if identifier in used:
            raise ValueError('tally_id已存在：' + str(identifier))
        filters = []
        for spec in params.get('filters') or []:
            kind = spec['type']
            if kind == 'energy':
                filters.append(openmc.EnergyFilter(spec['bins']))
            elif kind == 'material':
                filters.append(openmc.MaterialFilter(spec['bins']))
            elif kind == 'particle':
                filters.append(openmc.ParticleFilter(spec['bins']))
            elif kind == 'mesh':
                mesh = openmc.RegularMesh(mesh_id=spec.get('mesh_id'))
                mesh.dimension = tuple(spec['dimension'])
                mesh.lower_left = tuple(spec['lower_left'])
                mesh.upper_right = tuple(spec['upper_right'])
                filters.append(openmc.MeshFilter(mesh))
            else:
                raise ValueError('不支持的filter类型：' + str(kind))
        tally = openmc.Tally(tally_id=identifier, name=params.get('name') or '',
                             scores=list(params['scores']), filters=filters)
        tallies.append(tally)
        tallies.export_to_xml(str(tallies_path))
        return {'tally_id': identifier, 'name': tally.name, 'scores': list(tally.scores),
                'filters': [{'type': type(f).__name__, 'description': str(f)[:2000]} for f in filters],
                'tally_count': len(tallies),
                'note': '已写入tallies.xml；计分口径为OpenMC原始归一化，未换算功率或实际源强。'}
    raise ValueError('Unsupported OpenMC API action')


if __name__ == '__main__':
    try:
        params = json.load(sys.stdin)
        # Keep OpenMC's informational prints out of the JSON transport.
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(sys.argv[1], params)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, default=numpy_scalar))
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        sys.exit(1)
