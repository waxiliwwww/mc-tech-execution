"""Small process boundary around public OpenMC 0.16 APIs, also usable in a container."""
from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def numpy_scalar(value):
    import numpy as np
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError('Unsupported JSON result type: ' + type(value).__name__)


def _mesh_bounds(mesh):
    """Return regular-mesh bounds as 3-D cm, padding missing dimensions with zero."""
    lower = [float(v) for v in mesh.lower_left]
    upper = [float(v) for v in mesh.upper_right]
    while len(lower) < 3:
        lower.append(0.0)
    while len(upper) < 3:
        upper.append(0.0)
    return lower, upper


def filter_summary(item):
    """Compact scientific meaning; never dump an SDK object's repr to clients."""
    kind = type(item).__name__
    count = int(item.num_bins)
    parameters = {}
    def number(value):
        value = float(value)
        return value if math.isfinite(value) else None
    description = kind + '：' + str(count) + '个分箱'
    if kind == 'MeshFilter':
        mesh = item.mesh
        parameters = {'mesh_id': int(mesh.id), 'mesh_type': type(mesh).__name__}
        if type(mesh).__name__ == 'RegularMesh':
            dim = [int(v) for v in mesh.dimension]
            while len(dim) < 3:
                dim.append(1)
            lower, upper = _mesh_bounds(mesh)
            parameters.update(dimension=dim,
                              lower_left=[number(v) for v in lower],
                              upper_right=[number(v) for v in upper], unit='cm',
                              bin_indexing='data-array flat = i0 + nx*j0 + nx*ny*k0 (i0 fastest); filter_selection.bins uses OpenMC MeshFilter 1-based [i,j,k]',
                              filter_bin_convention='1-based [i,j,k]; valid i=1..nx, j=1..ny, k=1..nz',
                              bin_range=('data 0..' + str(count - 1) + '; filter bins [1..'
                                         + str(dim[0]) + ',1..' + str(dim[1]) + ',1..'
                                         + str(dim[2]) + ']') if count else 'empty')
            description = ('规则网格 ' + '×'.join(str(v) for v in dim)
                           + '，坐标单位cm；数据数组按扁平序号0..' + str(max(count - 1, 0))
                           + '，但filter_selection.bins要写OpenMC的1-based [i,j,k]三元组，'
                           '不是mesh id')
    elif kind in ('EnergyFilter', 'EnergyoutFilter'):
        edges = item.values
        parameters = {'bounds_preview': [number(v) for v in edges[:20]], 'bounds_count': len(edges),
                      'lower_bound': number(edges[0]), 'upper_bound': number(edges[-1]),
                      'bounds_truncated': len(edges) > 20, 'unit': 'eV'}
        description = ('入射' if kind == 'EnergyFilter' else '出射') + '能量：' + str(count) + '个区间，单位eV'
    elif kind in ('MaterialFilter', 'ParticleFilter', 'CellFilter', 'UniverseFilter'):
        values = item.bins
        preview = values[:20].tolist() if hasattr(values, 'tolist') else list(values[:20])
        parameters = {'bins_preview': preview, 'bins_truncated': count > 20}
        label = {'MaterialFilter': '材料编号', 'ParticleFilter': '粒子类型',
                 'CellFilter': '栅元编号', 'UniverseFilter': '宇宙编号'}[kind]
        description = label + '：' + '、'.join(str(v) for v in preview) + ('…' if count > 20 else '')
    return {'type': kind, 'description': description, 'bin_count': count, 'parameters': parameters}


def filter_identity(item):
    """Hash complete bin meaning, not truncated previews or assigned IDs."""
    import hashlib
    context = {}
    kind = type(item).__name__
    if kind == 'CellInstanceFilter':
        # Instance numbers need a verified geometry mapping, absent from the
        # standalone StatePoint reader. Do not invent a spatial equivalence.
        return None
    if kind == 'DistribcellFilter':
        try:paths = list(item.paths)
        except (AttributeError, ValueError):return None
        if len(paths) != item.num_bins or not all(isinstance(p, str) and p for p in paths):return None
        context['instance_paths'] = paths
    element = item.to_xml_element()
    element.attrib.pop('id', None)
    mesh = getattr(item, 'mesh', None)
    if mesh is not None:
        # A MeshFilter's bins element references a mesh ID; expand its physical
        # definition so renumbering alone does not make equivalent bins differ.
        if 'mesh' in element.attrib:
            # MeshMaterialFilter bins are (element, material), not a mesh ID.
            element.attrib.pop('mesh')
        else:
            bins = element.find('bins')
            if bins is not None:element.remove(bins)
        definition = mesh.to_xml_element()
        definition.attrib.pop('id', None)
        if type(mesh).__name__ == 'UnstructuredMesh':
            # The external mesh cannot be identified from its filename alone.
            filename = Path(mesh.filename)
            if not filename.is_file():return None
            digest = hashlib.sha256()
            with filename.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):digest.update(block)
            child = definition.find('filename')
            if child is not None:child.text = 'sha256:' + digest.hexdigest()
        element.append(definition)
    # XML indentation and attribute ordering are representation, not bin meaning.
    def normalized(node):
        return [node.tag, sorted(node.attrib.items()), (node.text or '').strip(),
                [normalized(child) for child in node]]
    return hashlib.sha256(json.dumps([normalized(element), context], sort_keys=True).encode()).hexdigest()


def _generation_keff(point):
    """调用方确认Combined缺失后，计算逐批k的合并估计；明确失效时不调用。
    与openmc_plot_convergence同口径（活跃批均值±标准误），避免同一作业的k在两处不一致。"""
    import numpy as np
    generations = np.asarray(getattr(point, 'k_generation', None), dtype=float).flatten()
    if generations.size == 0:
        return None
    inactive = int(getattr(point, 'n_inactive', 0) or 0)
    active = generations[inactive:]
    active = active[np.isfinite(active)]
    if active.size == 0:
        return None
    stderr = float(np.std(active, ddof=1) / np.sqrt(active.size)) if active.size > 1 else None
    return {'mean': float(np.mean(active)), 'std_dev': stderr,
            'source': 'statepoint:k_generation', 'batches_used': int(active.size),
            'note': 'k_combined/std不可用，改用逐批k合并估计；与openmc_plot_convergence同口径。'}


def statepoint_keff(point):
    """Single final-k policy for status, result reading and report consumers."""
    import math
    if point.run_mode != 'eigenvalue': return {'keff':None,'warnings':[]}
    value = point.keff
    pair = [float(value.nominal_value), float(value.std_dev)]
    clean = [v if math.isfinite(v) else None for v in pair]
    block = {'mean':clean[0], 'std_dev':clean[1], 'source':'statepoint:k_combined'}
    warnings = []
    if None in clean:
        fallback = _generation_keff(point)
        if fallback:
            fallback['fallback_from'] = 'statepoint的k_combined/std含非有限值'
            block = fallback
            warnings.append('keff：k_combined/std含非有限值，改用逐批k合并估计；不能据此判断是否收敛。')
        else:
            warnings.append('keff：k_combined/std含非有限值且没有可用的逐批k；mean/std保留为空，不替换为0，也不归因于未收敛。')
    if block.get('std_dev') == 0:
        warnings.append('keff标准差为0：估计器或采样退化，不能宣称高精度；摘要不播报为有效k结论。')
    return {'keff':block, 'warnings':warnings}


def _render_with_engine(here: Path, plot, params: dict, scratch, target: Path):
    """引擎直接读原XML出图：复制算例XML与依赖数据进scratch，写plots.xml后运行openmc --plot。
    用于SDK单文件导出丢宏观截面引用的多群算例。返回(是否成功, 失败原因)。"""
    try:
        import openmc
        # 独立子目录：SDK路径已经在scratch写了model.xml，引擎优先读它，
        # 那份单文件模型正好丢了<macroscopic>，会把同一个错误再犯一遍。
        folder = Path(scratch) / 'engine'
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(here.glob('*.xml')):
            if path.name != 'plots.xml':
                shutil.copy2(path, folder / path.name)
        for pattern in ('*.h5', '*.hdf5', '*.h5m'):
            for path in sorted(here.glob(pattern)):
                shutil.copy2(path, folder / path.name)
        # Same reserved-void normalization as the SDK path; only plotting scratch.
        import xml.etree.ElementTree as ET
        omitted = set(params.get('_plot_omitted_material_ids', []))
        if omitted:
            inputs = [folder/'model.xml'] if (folder/'model.xml').exists() else [folder/'materials.xml']
            for path in inputs:
                tree=ET.parse(path); root=tree.getroot()
                section=root.find('materials') if root.tag=='model' else root
                for item in list(section.findall('material')):
                    if int(item.get('id')) in omitted:section.remove(item)
                tree.write(path,encoding='utf-8',xml_declaration=True)
        openmc.Plots([plot]).export_to_xml(str(folder / 'plots.xml'))
        executable = params['_openmc_exec']
        command = ([str(executable)] if isinstance(executable, str)
                   else [str(item) for item in executable]) + ['--plot']
        completed = subprocess.run(command, cwd=str(folder), capture_output=True, text=True,
                                   timeout=300)
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or '').strip().splitlines()
            return False, ('引擎退出码' + str(completed.returncode) + '：'
                           + (tail[-1][:200] if tail else '无输出'))
        images = sorted(folder.glob('plot_*.png'))
        if not images:
            return False, '引擎退出码0但未生成plot_*.png'
        data = images[0].read_bytes()
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return False, '生成的plot文件不是PNG'
        target.write_bytes(data)
        return True, None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, type(exc).__name__ + '：' + str(exc)[:200]





def _mesh_descriptor(tally):
    """Describe a MeshFilter and whether flat bins have direct coordinates."""
    import numpy as np
    found = next((item for item in tally.filters if type(item).__name__ == 'MeshFilter'), None)
    if found is None:
        return None
    mesh = found.mesh
    if type(mesh).__name__ != 'RegularMesh':
        return {'id': int(mesh.id), 'type': type(mesh).__name__, 'coordinate_status': 'not_regular',
                'note': 'Only regular meshes can map flat bins to (i,j,k) and centers.'}
    dimension = [int(v) for v in mesh.dimension]
    while len(dimension) < 3:
        dimension.append(1)
    nx, ny, nz = dimension
    cells = nx * ny * nz
    shape = [int(v) for v in np.asarray(tally.mean).shape]
    direct = bool(shape and shape[0] == cells and all(v == 1 for v in shape[1:]))
    lower, upper = _mesh_bounds(mesh)
    return {'id': int(mesh.id), 'type': 'RegularMesh', 'dimension': dimension,
            'lower_left': lower, 'upper_right': upper, 'unit': 'cm',
            'cells': cells, 'shape': shape,
            'indexing': ('flat = i + nx*j + nx*ny*k (C order, i fastest)' if direct else
                         'flat index includes non-mesh filter dimensions; select one bin before mapping'),
            'coordinate_status': 'mappable' if direct else 'not_direct'}


def _mesh_indices(mean, limit, descriptor):
    """Prefer nonzero finite mesh bins so a leading-zero slice is not taken for all data."""
    import numpy as np
    flat = np.asarray(mean, dtype=float).ravel()
    if not descriptor or descriptor.get('coordinate_status') != 'mappable':
        return list(range(min(flat.size, limit))), 'first_values'
    finite = np.isfinite(flat)
    nonzero = np.flatnonzero(finite & (flat != 0))
    zero = np.flatnonzero(finite & (flat == 0))
    nonfinite = np.flatnonzero(~finite)
    order = np.concatenate((nonzero, zero, nonfinite))
    return [int(v) for v in order[:limit]], 'mesh_nonzero_first'


def _mesh_points(mean, std, indices, descriptor):
    import numpy as np
    if not descriptor or descriptor.get('coordinate_status') != 'mappable':
        return None
    nx, ny, nz = descriptor['dimension']
    lower, upper = descriptor['lower_left'], descriptor['upper_right']
    flat_mean, flat_std = np.asarray(mean).ravel(), np.asarray(std).ravel()
    points = []
    for index in indices:
        k, remainder = divmod(index, nx * ny)
        j, i = divmod(remainder, nx)
        center = [lower[0] + (i + .5) * (upper[0] - lower[0]) / nx,
                  lower[1] + (j + .5) * (upper[1] - lower[1]) / ny,
                  lower[2] + (k + .5) * (upper[2] - lower[2]) / nz]
        value, error = float(flat_mean[index]), float(flat_std[index])
        points.append({'flat_index': int(index), 'mesh_index': [int(i), int(j), int(k)],
                       'filter_bin': [int(i) + 1, int(j) + 1, int(k) + 1],
                       'center': [float(v) for v in center],
                       'mean': value if math.isfinite(value) else None,
                       'std_dev': error if math.isfinite(error) else None})
    return points
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
        if changes:
            write_parameters(model, {item['name']: item['after'] for item in changes})
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
            # 化学计量：O的质量份额按U质量折算出O/U=2（旧值0.1184对应O/U=1.76，与材料名不符）。
            moles_u = 0.0495 / 235.043930 + 0.9505 / 238.050788
            oxygen = round(2.0 * moles_u * 15.994915 / (0.0495 + 0.9505), 4)
            fuel.add_nuclide('O16', oxygen, 'wo')
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
            # HexLattice外径=环数×栅距；正六边形内切圆半径=边长×√3/2。
            # 旧实现直接给了"一个栅距"大小的棱柱，7根棒里6根的栅元中心落在反射边界外，
            # 实际只算了1根棒且不报错——这里按环数推导，并显式核对。
            rings = len(lattice.universes)
            outer_radius = rings * pitch
            edge = 2.0 * outer_radius / np.sqrt(3.0)
            prism = openmc.model.HexagonalPrism(edge_length=edge, orientation='y',
                                                boundary_type='reflective')
            inradius = edge * np.sqrt(3.0) / 2.0
            if inradius + 1e-9 < outer_radius:
                raise ValueError('六角边界内切圆半径小于栅格外径，栅元会被裁掉')
            geometry = openmc.Geometry([openmc.Cell(fill=lattice, region=-prism)])
            materials = openmc.Materials([fuel, clad, coolant])
            settings = openmc.Settings()
            model = openmc.Model(geometry, materials, settings)
            plot = openmc.Plot()
            plot.width = (2.2 * outer_radius, 2.2 * outer_radius)
            plot.pixels = (400, 400)
            plot.color_by = 'material'
            model.plots = openmc.Plots([plot])
            data_kind = 'public_parameters_self_test'
            note = ('公开参数六角栅格自测模型（7根钠冷燃料棒，HexLattice+反射六角棱柱，'
                    '棱柱按栅格外径推导：内切圆半径=' + format(outer_radius, '.4g') + 'cm）；'
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
        lattice_detail = []
        for value in lattices.values():
            entry = {'type': type(value).__name__}
            if hasattr(value, 'num_rings'):
                entry.update(n_rings=int(value.num_rings),
                             n_axial=int(getattr(value, 'num_axial', 1) or 1),
                             orientation=getattr(value, 'orientation', None),
                             pitch=[float(v) for v in np.atleast_1d(value.pitch)])
            else:
                entry['shape'] = [int(v) for v in getattr(value, 'shape', []) or []]
            lattice_detail.append(entry)
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
        from openmc_scientific_checks import safe_assess, advisories_warnings
        science = safe_assess(model)
        return {'scientific_checks':science,'warnings':advisories_warnings(science), 'api_parsed': True, 'cells': len(model.geometry.get_all_cells()),
                'materials': len(model.materials), 'tallies': len(model.tallies),
                'note': '公开API能够读取模型；未进行几何重叠、核数据完备性或物理正确性判定。'}
    if action == 'result-keff':
        with openmc.StatePoint(params['statepoint'], autolink=False) as point:
            return statepoint_keff(point)
    if action == 'results':
        limit = params.get('max_values', 1000)
        warnings = []
        def values(array, cap=limit, scope='tally'):
            flat = np.asarray(array).flatten()
            result = []
            for number in flat[:cap]:
                if np.isfinite(number):
                    result.append(float(number))
                else:
                    result.append(None)
                    warning = scope + '：统计量包含非有限值；保留为空，未替换为0；需检查统计条件。'
                    if warning not in warnings:
                        warnings.append(warning)
            return result
        with openmc.StatePoint(params['statepoint'], autolink=False) as point:
            import numpy as np
            keff = point.keff if point.run_mode == 'eigenvalue' else None
            tallies = []
            for tally in point.tallies.values():
                mesh = _mesh_descriptor(tally)
                indices, policy = _mesh_indices(tally.mean, limit, mesh)
                selected = [int(index) for index in indices]
                preview = values([tally.mean.ravel()[index] for index in selected], cap=limit, scope='tally')
                preview_std = values([tally.std_dev.ravel()[index] for index in selected], cap=limit, scope='tally')
                flat = np.asarray(tally.mean).ravel()
                zero_unscored = int(np.count_nonzero(np.isfinite(flat) & (flat == 0) & (np.asarray(tally.std_dev).ravel() == 0)))
                if zero_unscored:
                    warnings.append(f'tally {tally.id}：{zero_unscored}/{flat.size}个计分值均值和标准差均为0；可能是无贡献、统计不足或多群分箱错配，不等于物理量为零；请核对滤波边界和原始数据。')
                tallies.append({'id': tally.id, 'name': tally.name, 'scores': tally.scores,
                                'nuclides': tally.nuclides, 'estimator': tally.estimator,
                                'filters': [filter_summary(f) for f in tally.filters],
                                'filter_signatures': [filter_identity(f) for f in tally.filters],
                                'shape': list(tally.mean.shape), 'mean': preview,
                                'std_dev': preview_std, 'preview_indices': selected,
                                'preview_policy': policy,
                                'zero_mean_std_values': zero_unscored,
                                'zero_values': int(np.count_nonzero(np.isfinite(flat) & (flat == 0))),
                                'nonzero_values': int(np.count_nonzero(np.isfinite(flat) & (flat != 0))),
                                'non_finite_values': int(np.count_nonzero(~np.isfinite(flat))),
                                'values_truncated': tally.mean.size > limit,
                                'total_values': int(tally.mean.size), 'returned_values': len(preview),
                                'mesh': mesh,
                                'preview_points': _mesh_points(tally.mean, tally.std_dev, selected, mesh),
                                'normalization': 'OpenMC原始计分归一化；未换算为工程功率或实际源强。'})
            selected_keff = statepoint_keff(point)
            keff_block = selected_keff['keff']
            warnings.extend(selected_keff['warnings'])
            from openmc_scientific_checks import assess_directory, advisories_warnings
            science = assess_directory()
            warnings.extend(advisories_warnings(science))
            return {'scientific_checks':science, 'openmc_version': list(point.version), 'run_mode': point.run_mode,
                    'particles': point.n_particles, 'realizations': point.n_realizations,
                    'keff': keff_block,
                    'tallies': tallies, 'max_values_per_tally': limit, 'warnings': warnings}
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
        box = universe.bounding_box
        extent = np.asarray(box.upper_right) - np.asarray(box.lower_left)
        if not np.isfinite(extent).all() or not (extent > 0).all():
            raise ValueError('DAGMC几何包围盒须为有限且各方向具有正尺寸，无法生成真空边界')
        padding = float(max(extent) * 0.1)
        geometry = openmc.Geometry(root=universe.bounded_universe(
            boundary_type='vacuum', padding_distance=padding))
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
                'boundary': {'type': 'vacuum', 'shape': 'box', 'padding_cm': padding,
                             'geometry_lower_left_cm': [float(v) for v in box.lower_left],
                             'geometry_upper_right_cm': [float(v) for v in box.upper_right]},
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
        # DAGMC reserved mat:graveyard/void/vacuum tags are engine void, not
        # physical materials. Omit redundant *empty* declarations in scratch;
        # retain any material referenced by a real CSG cell/override.
        dag = [u for u in model.geometry.get_all_universes().values() if isinstance(u, openmc.DAGMCUniverse)]
        referenced = set(model.geometry.get_all_materials())
        for universe in dag:
            for material in getattr(universe,'material_overrides',{}).values():
                for item in (material if isinstance(material,list) else [material]):
                    if item is not None:referenced.add(item.id)
        omitted = [m.id for m in model.materials if dag and m.name.lower() in ('graveyard','void','vacuum')
                   and m.id not in referenced and not m.get_nuclides() and not getattr(m,'macroscopic',None)]
        if omitted:
            model.materials = openmc.Materials([m for m in model.materials if m.id not in omitted])
        params = {**params, '_plot_omitted_material_ids':omitted}
        supplied_origin = params.get('origin')
        supplied_width = params.get('width')
        if supplied_origin is not None and supplied_width is not None:
            plot = openmc.SlicePlot()
            plot.basis = basis
        else:
            # from_geometry is a factory: discarding its return value silently
            # leaves SlicePlot's default 4 cm width. Only the plane must be finite.
            slice_axis = {'xy': 2, 'xz': 1, 'yz': 0}[basis]
            plot = openmc.SlicePlot.from_geometry(model.geometry, basis=basis,
                slice_coord=0. if supplied_origin is None else supplied_origin[slice_axis])
        plot.pixels = (pixels, pixels)
        plot.color_by = color_by
        if supplied_width is not None:
            width = [float(v) for v in supplied_width]
            plot.width = tuple(width * 2 if len(width) == 1 else width)
        if supplied_origin is not None:
            plot.origin = tuple(float(v) for v in supplied_origin)
        target = here / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        # The SDK writes plots.xml and renderer output into cwd. Export a
        # plotting-only model into disposable scratch; never touch case inputs.
        for universe in model.geometry.get_all_universes().values():
            if isinstance(universe, openmc.DAGMCUniverse):
                universe.filename = str((here / universe.filename).resolve())
        if model.materials.cross_sections:
            model.materials.cross_sections = str((here / model.materials.cross_sections).resolve())
        with tempfile.TemporaryDirectory(prefix='mc-view-') as scratch:
            try:
                isolated = openmc.Model(model.geometry, model.materials, model.settings)
                isolated.export_to_model_xml(Path(scratch) / 'model.xml')
                image = plot.to_ipython_image(openmc_exec=params['_openmc_exec'], cwd=scratch)
                data = getattr(image, 'data', None)
                if not isinstance(data, (bytes, bytearray)) or data[:8] != b'\x89PNG\r\n\x1a\n':
                    raise ValueError('渲染完成但未得到PNG数据')
                target.write_bytes(bytes(data))
                method, fallback_note = 'python_api_export', None
            except Exception as first_error:
                # 多群算例经SDK单文件导出会丢掉<macroscopic>，引擎随即以
                # 'No macroscopic data or nuclides specified on material 1' 中止。
                # 改为把原XML与依赖数据复制进scratch，让引擎自己读plots.xml出图。
                detail = str(first_error).strip().splitlines()[0][:160] if str(first_error).strip() else type(first_error).__name__
                rendered, engine_detail = _render_with_engine(here, plot, params, scratch, target)
                if rendered:
                    method = 'engine_native_plot_xml'
                    fallback_note = ('SDK导出渲染失败（' + type(first_error).__name__ + '：' + detail
                                     + '），已改用引擎直接读原XML出图。')
                else:
                    raise ValueError('切片图渲染失败；SDK路径：' + detail
                                     + '；引擎直接绘图：' + (engine_detail or '未知原因')) from first_error
        width = plot.width
        origin = plot.origin
        return {'image': filename, 'basis': basis, 'pixels': pixels, 'color_by': color_by,
                'render_method': method,
                'width': [float(v) for v in width],
                'origin': [float(v) for v in origin],
                'width_source': 'user' if supplied_width is not None else 'geometry_bounding_box',
                'origin_source': 'user' if supplied_origin is not None else 'bounding_box_plane_center_slice_0',
                'input_files_modified': False,
                'plotting_omitted_reserved_void_material_ids': omitted,
                'coverage_verified': False,
                'coverage_note': '切片只显示当前平面；空白可能是域外或未填充区域，不证明几何覆盖。用geometry_check核对输运路径；不自动补材料。',
                'materials': len(model.materials),
                'cells': len(model.geometry.get_all_cells()),
                'note': '切片图由OpenMC引擎绘制；宽度/原点单位cm，显示范围以返回的width/origin为准。'
                        + ('（' + fallback_note + '）' if fallback_note else '')}
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
        requested = params.get('key_nuclides') or ['U235', 'U238', 'Pu239', 'Pu241', 'Xe135', 'Sm149']
        key_nuclides, unavailable = {}, []
        for nucleus in requested:
            found = False
            for identifier in material_ids:
                try:
                    values = data.get_mass(str(identifier), nucleus, mass_units='g')[1]
                except (ValueError, KeyError):
                    continue
                if any(np.isfinite(values)):
                    key_nuclides.setdefault(nucleus, {})[str(identifier)] = [float(v) for v in values]
                    found = True
            if not found:
                unavailable.append(nucleus)
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
                'key_nuclides_requested': requested, 'key_nuclides_unavailable': unavailable,
                'depletion_guard': spec.get('guard'),
                'chain_file': chain, 'note': '燃耗值按功率×时间/初始重金属质量估算（MWd/kgHM）；'
                                            '核素质量为该材料的质量，非密度或份额；Xe135/Sm149是热谱燃耗'
                                            '常见毒物，若链/材料没有则列入unavailable，不伪造0。'}
    if action in ('add-tally','update-tally','delete-tally'):
        from openmc_tally_ops import mutate
        return mutate({**params,'operation':{'add-tally':'add','update-tally':'update','delete-tally':'delete'}[action]})
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
