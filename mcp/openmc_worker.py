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
        marker = {'template': template, 'data_kind': data_kind, 'openmc_version': openmc.__version__,
                  'professional_acceptance': False, 'note': note}
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
