"""Small process boundary around public OpenMC 0.16 APIs, also usable in a container."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


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
    if action == 'build-model':
        from openmc_model_spec import build
        return build(params['spec'])
    if action in ('plot-result', 'plot-series', 'plot-mesh', 'plot-convergence', 'render-report', 'select-series'):
        from openmc_visuals import draw, plot_convergence, plot_mesh, plot_result, render_docx, selected_series
        if action == 'plot-mesh':return plot_mesh(params)
        if action == 'plot-convergence':return plot_convergence(params)
        if action == 'plot-result':return plot_result(params)
        if action == 'plot-series':return draw(params['series'], Path(params['filename']), params)
        if action == 'render-report':return render_docx(params)
        with openmc.StatePoint(params['statepoint'], autolink=False) as point:
            return selected_series(point, params)
    if action in ('parameters', 'check-parameters', 'update-parameters'):
        from openmc_parameters import apply, checked, describe, load_model, write_parameters
        from openmc_model_spec import checked_declarative_spec, mapped_declarative_spec
        baseline=checked_declarative_spec() if action != 'parameters' else None
        model = load_model()
        if action == 'parameters':
            return {'parameters': describe(model), 'note': '仅列当前适配器支持的公开属性；sum密度及未适配表面不提供修改入口。'}
        if action == 'check-parameters':
            for values in params['combinations']:
                checked(model, values)
                if baseline is not None:mapped_declarative_spec(baseline,values)
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
        elif template in ('assembly-17x17', 'core-lattice'):
            import hashlib
            is_assembly = template == 'assembly-17x17'
            model = openmc.examples.pwr_assembly() if is_assembly else openmc.examples.pwr_core()
            data_kind = 'public_benchmark_example'
            note = ('OpenMC官方BEAVRS反射17×17组件示例（公开基准几何与材料参数）；运行需真实核数据；'
                    '不是甲方组件参数，未作专业准确率验收。' if is_assembly else
                    'OpenMC官方OECD/NEA蒙卡性能基准全堆示例（21×21栅格）；规模大，仅供结构与接口验证；不是甲方模型。')
            plot = openmc.Plot()
            plot.width = (21.42, 21.42) if is_assembly else (400.0, 400.0)
            plot.pixels = (400, 400)
            plot.color_by = 'material'
            model.plots = openmc.Plots([plot])
            source_file = Path(openmc.examples.__file__)
            benchmark = {'name': 'BEAVRS 17x17 assembly' if is_assembly else 'OECD/NEA MC performance benchmark core',
                         'source_url': 'https://github.com/openmc-dev/openmc/blob/v' + openmc.__version__ + '/openmc/examples.py',
                         'source_sha256': hashlib.sha256(source_file.read_bytes()).hexdigest()}
        elif template == 'control-rod':
            # 公开文献常用压水堆参数自测：单燃料栅元+单控制棒（B4C）置于纯水中，反射边界。
            fuel = openmc.Material(name='UO2 3.1 w/o (public parameters)')
            fuel.set_density('g/cm3', 10.3)
            fuel.add_nuclide('U235', 0.031, 'wo')
            fuel.add_nuclide('U238', 0.969, 'wo')
            fuel.add_nuclide('O16', 0.1184, 'wo')
            clad = openmc.Material(name='Zircaloy-4 (public parameters)')
            clad.set_density('g/cm3', 6.55)
            for nuclide, fraction in (('Zr90', 0.5145), ('Zr91', 0.1122), ('Zr92', 0.1715),
                                      ('Zr94', 0.1738), ('Zr96', 0.0280)):
                clad.add_nuclide(nuclide, fraction, 'wo')
            absorber = openmc.Material(name='B4C absorber (public parameters)')
            absorber.set_density('g/cm3', 2.52)
            absorber.add_nuclide('B10', 0.199, 'ao')
            absorber.add_nuclide('B11', 0.801, 'ao')
            absorber.add_nuclide('C0', 1.0, 'ao')  # 天然碳：库内无 C12 单同位素条目
            water = openmc.Material(name='Light water 600K (public parameters)')
            water.set_density('g/cm3', 0.743)
            water.add_nuclide('H1', 2.0, 'ao')
            water.add_nuclide('O16', 1.0, 'ao')
            fuel_cyl = openmc.ZCylinder(x0=-0.63, y0=0.0, r=0.4096)
            fuel_clad = openmc.ZCylinder(x0=-0.63, y0=0.0, r=0.4750)
            rod_abs = openmc.ZCylinder(x0=0.63, y0=0.0, r=0.4300)
            rod_clad = openmc.ZCylinder(x0=0.63, y0=0.0, r=0.4750)
            box = openmc.model.RectangularParallelepiped(-1.26, 1.26, -1.26, 1.26, -10.0, 10.0,
                                                         boundary_type='reflective')
            cells = [openmc.Cell(fill=fuel, region=-fuel_cyl & -box),
                     openmc.Cell(fill=clad, region=+fuel_cyl & -fuel_clad & -box),
                     openmc.Cell(fill=absorber, region=-rod_abs & -box),
                     openmc.Cell(fill=clad, region=+rod_abs & -rod_clad & -box),
                     openmc.Cell(fill=water, region=+fuel_clad & +rod_clad & -box)]
            geometry = openmc.Geometry(cells)
            materials = openmc.Materials([fuel, clad, absorber, water])
            settings = openmc.Settings()
            model = openmc.Model(geometry, materials, settings)
            data_kind = 'public_parameters_self_test'
            note = ('公开文献常用压水堆栅元与控制棒参数自测模型（B4C吸收体，未加硼酸）；'
                    '不是甲方组件参数，未作专业准确率或控制棒价值验收。')
        elif template == 'pincell-depletion':
            import math
            fuel = openmc.Material(name='UO2 2.4 w/o (public parameters)')
            fuel.set_density('g/cm3', 10.29769)
            fuel.add_nuclide('U234', 5.5815e-05, 'wo')
            fuel.add_nuclide('U235', 0.024, 'wo')
            fuel.add_nuclide('U238', 0.9753, 'wo')
            fuel.add_nuclide('O16', 0.1184, 'wo')
            fuel.volume = math.pi * 0.4096 ** 2 * 20.0  # 燃耗要求可烧材料显式体积
            clad = openmc.Material(name='Zircaloy (public parameters)')
            clad.set_density('g/cm3', 6.55)
            clad.add_element('Zr', 0.98, 'wo')
            clad.add_element('Sn', 0.015, 'wo')
            clad.add_element('Fe', 0.002, 'wo')
            water = openmc.Material(name='Water 600K (public parameters)')
            water.set_density('g/cm3', 0.7)
            water.add_nuclide('H1', 2.0, 'ao')
            water.add_nuclide('O16', 1.0, 'ao')
            fuel_cyl = openmc.ZCylinder(r=0.4096)
            clad_cyl = openmc.ZCylinder(r=0.4750)
            box = openmc.model.RectangularParallelepiped(-0.63, 0.63, -0.63, 0.63, -10.0, 10.0,
                                                         boundary_type='reflective')
            cells = [openmc.Cell(fill=fuel, region=-fuel_cyl & -box),
                     openmc.Cell(fill=clad, region=+fuel_cyl & -clad_cyl & -box),
                     openmc.Cell(fill=water, region=+clad_cyl & -box)]
            geometry = openmc.Geometry(cells)
            materials = openmc.Materials([fuel, clad, water])
            settings = openmc.Settings()
            model = openmc.Model(geometry, materials, settings)
            data_kind = 'public_parameters_self_test'
            note = ('公开压水堆栅元参数自测模型（2.4% UO2，燃料体积按πr²h显式计算，可做燃耗）；'
                    '不是甲方组件参数，未作专业准确率或燃耗验收。')
        elif template == 'hex-lattice':
            # HexLattice自测：7根燃料棒六角栅格，钠冷，反射边界，2D。
            fuel = openmc.Material(name='UO2 4.95 w/o (public parameters)')
            fuel.set_density('g/cm3', 10.4)
            fuel.add_nuclide('U235', 0.0495, 'wo')
            fuel.add_nuclide('U238', 0.9505, 'wo')
            fuel.add_nuclide('O16', 0.1184, 'wo')
            clad = openmc.Material(name='Fe-15Cr steel (public parameters)')
            clad.set_density('g/cm3', 7.7)
            clad.add_element('Fe', 0.85, 'wo')
            clad.add_element('Cr', 0.15, 'wo')
            coolant = openmc.Material(name='Sodium coolant (public parameters)')
            coolant.set_density('g/cm3', 0.85)
            coolant.add_nuclide('Na23', 1.0)
            fuel_cyl = openmc.ZCylinder(r=0.45)
            clad_cyl = openmc.ZCylinder(r=0.52)
            pin_universe = openmc.Universe(cells=[
                openmc.Cell(fill=fuel, region=-fuel_cyl),
                openmc.Cell(fill=clad, region=+fuel_cyl & -clad_cyl),
                openmc.Cell(fill=coolant, region=+clad_cyl)])
            coolant_universe = openmc.Universe(cells=[openmc.Cell(fill=coolant)])
            pitch = 1.5
            lattice = openmc.HexLattice()
            lattice.center = (0.0, 0.0)
            lattice.pitch = (pitch,)
            lattice.orientation = 'y'
            # 由外到内逐环，每环从"顶部"顺时针：7根=外环6+中心1。
            lattice.universes = [[pin_universe] * 6, [pin_universe]]
            lattice.outer = coolant_universe
            prism = openmc.model.HexagonalPrism(edge_length=pitch / np.sqrt(3.0), orientation='y',
                                                boundary_type='reflective')
            geometry = openmc.Geometry([openmc.Cell(fill=lattice, region=-prism)])
            materials = openmc.Materials([fuel, clad, coolant])
            settings = openmc.Settings()
            model = openmc.Model(geometry, materials, settings)
            data_kind = 'public_parameters_self_test'
            note = ('公开参数六角栅格自测模型（7根钠冷燃料棒，HexLattice+反射六角棱柱）；'
                    '不是甲方组件参数，未作专业准确率验收。')
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
        lattices = model.geometry.get_all_lattices()
        lattice_detail = [{'type': type(value).__name__,
                           'shape': [int(v) for v in getattr(value, 'shape', []) or []]}
                          for value in lattices.values()]
        marker = {'template': template, 'data_kind': data_kind, 'openmc_version': openmc.__version__,
                  'professional_acceptance': False, 'note': note}
        if lattice_detail:
            marker['lattices'] = lattice_detail
        if template in ('assembly-17x17', 'core-lattice'):
            marker.update(benchmark=benchmark)
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
    if action == 'view-geometry':
        basis = params.get('basis') or 'xy'
        if basis not in ('xy', 'xz', 'yz'):
            raise ValueError('basis须为xy/xz/yz')
        pixels = params.get('pixels') or 400
        if type(pixels) is not int or not 50 <= pixels <= 1200:
            raise ValueError('pixels须为50..1200的整数')
        color_by = params.get('color_by') or 'material'
        if color_by not in ('material', 'cell'):
            raise ValueError('color_by须为material或cell')
        filename = params.get('filename') or 'slice.png'
        model = (openmc.Model.from_model_xml('model.xml') if Path('model.xml').exists()
                 else openmc.Model.from_xml())
        here = Path.cwd()
        before = {path.name for path in here.iterdir()}
        plot = openmc.SlicePlot()
        plot.basis = basis
        plot.pixels = (pixels, pixels)
        plot.color_by = color_by
        if params.get('width') is not None:
            plot.width = tuple(float(v) for v in params['width'])
        if params.get('origin') is not None:
            plot.origin = tuple(float(v) for v in params['origin'])
        plot.from_geometry(model.geometry)
        image = plot.to_ipython_image(openmc_exec=params['_openmc_exec'])
        target = here / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        data = getattr(image, 'data', None)
        if isinstance(data, (bytes, bytearray)) and data[:8] == b'\x89PNG\r\n\x1a\n':
            target.write_bytes(bytes(data))
        else:
            produced = sorted(here.glob('plot*.png'), key=lambda path: path.stat().st_mtime)
            if not produced:
                raise ValueError('渲染完成但未得到PNG数据')
            shutil.copyfile(produced[-1], target)
        for path in here.glob('plot*.png'):
            if path.name not in before:
                path.unlink()
        plots_xml = here / 'plots.xml'
        if plots_xml.is_file() and 'plots.xml' not in before:
            plots_xml.unlink()
        width = plot.width
        origin = plot.origin
        return {'image': filename, 'basis': basis, 'pixels': pixels, 'color_by': color_by,
                'width': list(width) if isinstance(width, (tuple, list)) else width,
                'origin': list(origin) if isinstance(origin, (tuple, list)) else origin,
                'materials': len(model.materials),
                'cells': len(model.geometry.get_all_cells()),
                'note': '切片图由OpenMC引擎绘制；宽度/原点单位cm，显示范围以返回的width/origin为准。'}
    if action == 'depletion-results':
        import xml.etree.ElementTree as ET
        from openmc.deplete import Results as DepletionResults
        path = Path(params.get('file') or 'depletion_results.h5')
        if not path.is_file():
            raise ValueError('缺少depletion_results.h5；确认作业为mode=deplete且已完成')
        data = DepletionResults(str(path))
        times, keff = data.get_keff(time_units='d')
        marker = Path('mc-case.json')
        spec = json.loads(marker.read_text(encoding='utf-8')).get('depletion', {}) if marker.is_file() else {}
        material_ids = []
        materials_path = Path('materials.xml')
        if materials_path.is_file():
            for node in ET.parse(str(materials_path)).getroot().iter('material'):
                identifier = node.get('id')
                if identifier:
                    material_ids.append(int(identifier))
        heavy_symbols = {'U', 'Np', 'Pu', 'Am', 'Cm', 'Bk', 'Cf', 'Es', 'Fm', 'Md', 'No', 'Lr'}
        chain = spec.get('chain_file')
        heavy = []
        if chain and Path(chain).is_file():
            for node in ET.parse(chain).getroot().iter('nuclide'):
                name = node.get('name') or ''
                symbol = ''.join(ch for ch in name if ch.isalpha())
                if symbol in heavy_symbols:
                    heavy.append(name)
        heavy_mass = {}
        for identifier in material_ids:
            total_g = 0.0
            for nucleus in heavy:
                try:
                    mass = data.get_mass(str(identifier), nucleus, mass_units='g')[1]
                except (ValueError, KeyError):
                    continue
                if np.isfinite(mass[0]):
                    total_g += float(mass[0])
            if total_g > 0:
                heavy_mass[str(identifier)] = total_g
        total_heavy_g = sum(heavy_mass.values())
        key_nuclides = {}
        for nucleus in ('U235', 'U238', 'Pu239', 'Pu241'):
            if nucleus not in heavy:
                continue
            for identifier in material_ids:
                try:
                    values = data.get_mass(str(identifier), nucleus, mass_units='g')[1]
                except (ValueError, KeyError):
                    continue
                if any(np.isfinite(values)):
                    key_nuclides.setdefault(nucleus, {})[str(identifier)] = [float(v) for v in values]
        steps = []
        power = spec.get('power_watts')
        for index in range(len(times)):
            step = {'step': index, 'time_days': round(float(times[index]), 6),
                    'keff': {'mean': float(keff[index][0]), 'std_dev': float(keff[index][1])}}
            if power and total_heavy_g > 0:
                megawatt_days = float(power) * float(times[index]) * 86400.0 / 1.0e6 / 86400.0
                step['burnup_mwd_kg'] = round(megawatt_days / (total_heavy_g / 1000.0), 6)
            steps.append(step)
        return {'file': path.name, 'times_days': [round(float(v), 6) for v in times], 'steps': steps,
                'heavy_metal_mass_g': {key: round(value, 3) for key, value in heavy_mass.items()},
                'heavy_metal_total_g': round(total_heavy_g, 3),
                'nuclide_mass_g': key_nuclides,
                'chain_file': chain, 'note': '燃耗值按功率×时间/初始重金属质量估算（MWd/kgHM）；'
                                            '核素质量为该材料的质量，非密度或份额。'}
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
