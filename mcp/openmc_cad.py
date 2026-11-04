"""CAD→DAGMC conversion tools: independent cadenv subprocess bridge; no openmc/cadquery here.

转换链工程验证与版本组合见 docs/adr/0002。产物为工程验证件，不是专业验收：
材料指派、单位换算与网格质量都需业务确认；可运行模型还需另行生成 XML。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from openmc_runtime import (ROOT, Error, api, atomic_json, config, file_location,
                             path_in_workspace, provenance, workspace)

STEP_SUFFIXES = ('.step', '.stp')
MAX_STEP_BYTES = 200 * 1024 * 1024
UNITS_NOTE = ('STEP内部长度通常为毫米，OpenMC使用厘米；scale_factor取值已写入溯源记录，'
              '单位换算是否正确需业务确认。')


def _cad_command(cfg: dict):
    command = cfg.get('cad_python_command')
    if not isinstance(command, list) or not command:
        raise Error('CAD转换环境未配置：部署配置cad_python_command须为非空参数数组；'
                    '安装与系统库依赖见docs/adr/0002')
    return command, cfg.get('cad_timeout_seconds', 900)


def _cad_env(cfg: dict) -> dict:
    env = dict(os.environ)
    library = cfg.get('cad_library_path')
    if library:
        env['LD_LIBRARY_PATH'] = library + os.pathsep + env.get('LD_LIBRARY_PATH', '')
    return env


def _cad_api(action: str, payload: dict, cfg: dict, cwd: Path) -> dict:
    """结果经结果文件回传：网格后端（Rust）可能直接写fd 1，stdout不作为JSON通道。"""
    command, timeout = _cad_command(cfg)
    fd, result_name = tempfile.mkstemp(prefix='mc-cad-result-', suffix='.json')
    os.close(fd)
    result_path = Path(result_name)
    try:
        try:
            completed = subprocess.run(
                command + [str(ROOT / 'mcp/cad_worker.py'), action], cwd=str(cwd),
                input=json.dumps({**payload, '_result_path': result_name}, allow_nan=False),
                text=True, capture_output=True, timeout=timeout, env=_cad_env(cfg))
        except FileNotFoundError as exc:
            raise Error('CAD转换程序不存在；检查cad_python_command配置') from exc
        except subprocess.TimeoutExpired as exc:
            raise Error(f'CAD转换调用超时（超过{timeout}秒）；可加粗网格限制或简化模型') from exc
        try:
            value = json.loads(result_path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError
        except (OSError, ValueError):
            lines = ((completed.stderr or '').strip().splitlines()
                     or (completed.stdout or '').strip().splitlines())
            raise Error('CAD转换失败：' + (lines[-1][:1000] if lines else 'worker未留下结果文件'))
        if not value.get('ok'):
            raise Error('CAD转换失败：' + str(value.get('error', 'unknown error'))[:1000])
        return value['result']
    finally:
        if result_path.exists():
            result_path.unlink()


def _step_file(step_path: str) -> Path:
    source = path_in_workspace(step_path)
    if not source.is_file():
        raise Error('step_path须为文件')
    if source.suffix.lower() not in STEP_SUFFIXES:
        raise Error('仅接受.step/.stp文件：' + source.name)
    if source.stat().st_size > MAX_STEP_BYTES:
        raise Error('STEP文件超过200MiB上限')
    return source


def _positive(value, name: str):
    if type(value) not in (int, float) or isinstance(value, bool) or value <= 0:
        raise Error(name + '须为正数')
    return value


def inspect_step(step_path: str) -> dict:
    cfg = config()
    source = _step_file(step_path)
    value = _cad_api('inspect-step', {'step': str(source)}, cfg, source.parent)
    return {'step_path': step_path, 'source': file_location(source),
            'solids': value['solids'], 'bounding_box': value.get('bounding_box'),
            'versions': value.get('versions'), 'units_note': UNITS_NOTE,
            'workspace': str(workspace()), 'location': 'server',
            'note': '仅解析CAD结构与实体计数，不作单位换算；转换时material_tags需按实体数逐一给标签。'}


def convert_cad(step_path: str, target_dir: str, material_tags, scale_factor: float = 1.0,
                min_mesh_size: float | None = None, max_mesh_size: float | None = None,
                implicit_complement_material_tag: str | None = None) -> dict:
    cfg = config()
    source = _step_file(step_path)
    directory = path_in_workspace(target_dir, exists=False)
    if directory.exists():
        raise Error('目标目录已存在；请选择新目录，避免覆盖已有输入')
    if isinstance(material_tags, str):
        if material_tags not in ('assembly_materials', 'assembly_names'):
            raise Error('material_tags为字符串时只能是assembly_materials或assembly_names；'
                        '普通STEP请按实体数给标签列表（先用openmc_inspect_step查询）')
    else:
        if (not isinstance(material_tags, list) or not material_tags
                or any(not isinstance(tag, str) or not tag.strip() for tag in material_tags)):
            raise Error('material_tags须为非空字符串列表，每个实体一个材料标签')
        if len(set(material_tags)) != len(material_tags):
            raise Error('material_tags不能包含重复标签')
    _positive(scale_factor, 'scale_factor')
    payload = {'step': str(source), 'material_tags': material_tags,
               'scale_factor': scale_factor, 'output': str(directory / 'dagmc.h5m')}
    for key, value in (('min_mesh_size', min_mesh_size), ('max_mesh_size', max_mesh_size)):
        if value is not None:
            payload[key] = _positive(value, key)
    if implicit_complement_material_tag is not None:
        if not isinstance(implicit_complement_material_tag, str) or not implicit_complement_material_tag.strip():
            raise Error('implicit_complement_material_tag须为非空字符串或省略')
        payload['implicit_complement_material_tag'] = implicit_complement_material_tag
    directory.mkdir(parents=True)
    try:
        conversion = _cad_api('convert', payload, cfg, directory)
    except Error:
        # 保留失败现场供诊断，与create_model约定一致；不删除用户可见证据。
        atomic_json(directory / 'conversion-failed.json',
                    {'state': 'failed', 'note': 'CAD转换失败；目录内容不能作为有效DAGMC几何使用。'})
        raise
    output = directory / 'dagmc.h5m'
    try:
        engine = {'ok': True, **api('inspect-h5m', directory, {'filename': 'dagmc.h5m'}, cfg)}
    except Error as exc:
        # 引擎读回失败不否定转换产物本身；错误文本保留，不静默。
        engine = {'ok': False, 'error': str(exc)[:500]}
    marker = {'data_kind': 'cad_conversion_unverified', 'generator': 'cad_to_dagmc',
              'source_step': {'path': step_path, 'sha256': conversion.get('source_sha256')},
              'material_tags': material_tags, 'scale_factor': scale_factor,
              'mesh_limits': {key: payload[key] for key in ('min_mesh_size', 'max_mesh_size') if key in payload},
              'implicit_complement_material_tag': implicit_complement_material_tag,
              'versions': conversion.get('versions'), 'h5m_sha256': conversion.get('sha256'),
              'professional_acceptance': False,
              'note': 'CAD转换产物未经专业校验：材料指派、单位换算与网格质量需业务确认；'
                      '运行计算还需生成模型XML（材料与设置）。'}
    atomic_json(directory / 'mc-case.json', marker)
    return {'case_dir': target_dir, 'h5m': file_location(output),
            'solids': conversion.get('solids'), 'sha256': conversion.get('sha256'),
            'size_bytes': conversion.get('size_bytes'), 'engine_read': engine,
            'versions': conversion.get('versions'), 'units_note': UNITS_NOTE,
            'workspace': str(workspace()), 'location': 'server',
            'provenance': provenance(directory, cfg), 'professional_acceptance': False,
             'note': marker['note']}


# DAGMC由标签指派材料；这里只收可直接set_density的口径（sum需组成求和，另议）。
DAGMC_DENSITY_UNITS = ('g/cm3', 'g/cc', 'atom/b-cm', 'atom/cm3')


def _materials_spec(value, required: list):
    """校验materials：名称须与转换时material_tags完全一致，密度正定、单位合法、核素可缺省如实告警。"""
    if not isinstance(value, list) or not value:
        raise Error('materials须为非空列表，每项含name/density/units（可选nuclides）')
    names = []
    for item in value:
        if not isinstance(item, dict):
            raise Error('materials每项须为对象：name/density/units[/nuclides]')
        name = item.get('name')
        if not isinstance(name, str) or not name.strip():
            raise Error('materials.name须为非空字符串')
        if name in names:
            raise Error('materials.name不能重复：' + name)
        names.append(name)
        density = item.get('density')
        if type(density) not in (int, float) or isinstance(density, bool) or not density > 0:
            raise Error('materials.density须为正数：' + name)
        if item.get('units') not in DAGMC_DENSITY_UNITS:
            raise Error('materials.units须为' + '/'.join(DAGMC_DENSITY_UNITS) + '之一：' + name)
        nuclides = item.get('nuclides')
        if nuclides is not None:
            if not isinstance(nuclides, list):
                raise Error('materials.nuclides须为列表：' + name)
            for entry in nuclides:
                if not isinstance(entry, dict):
                    raise Error('nuclides每项须为对象name/fraction[/percent_type]：' + name)
                if not isinstance(entry.get('name'), str) or not entry['name'].strip():
                    raise Error('nuclides.name须为非空字符串：' + name)
                fraction = entry.get('fraction')
                if type(fraction) not in (int, float) or isinstance(fraction, bool) or not fraction > 0:
                    raise Error('nuclides.fraction须为正数：' + name)
                if entry.get('percent_type', 'wo') not in ('wo', 'ao'):
                    raise Error('nuclides.percent_type只能是wo或ao：' + name)
    missing = [tag for tag in required if tag not in names]
    extra = [name for name in names if name not in required]
    if missing:
        raise Error('materials缺少转换时的材料标签：' + '、'.join(missing))
    if extra:
        raise Error('materials含转换时未指派的标签：' + '、'.join(extra))


def build_dagmc_model(case_dir: str, materials, particles: int = 200, batches: int = 10,
                      inactive: int = 2, source_point=None, seed=None) -> dict:
    """为已转换的DAGMC算例组装model.xml（材料/设置/源）；不覆盖已有XML，不重做转换。"""
    cfg = config()
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为已存在的转换目录')
    if not (directory / 'dagmc.h5m').is_file():
        raise Error('目录内没有dagmc.h5m；请先用openmc_convert_cad完成转换')
    if any((directory / name).exists() for name in ('model.xml', 'geometry.xml', 'materials.xml')):
        raise Error('目录已有模型XML；本工具不覆盖，请换新目录组装或先移走旧文件')
    marker_path = directory / 'mc-case.json'
    if not marker_path.is_file():
        raise Error('目录缺少mc-case.json溯源记录，不能确认材料标签')
    try:
        marker = json.loads(marker_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Error('mc-case.json损坏，不能确认材料标签') from exc
    if marker.get('generator') != 'cad_to_dagmc':
        raise Error('mc-case.json不是CAD转换产物，拒绝组装')
    required = list(marker.get('material_tags') or [])
    implicit = marker.get('implicit_complement_material_tag')
    if implicit:
        required.append(implicit)
    if not required:
        raise Error('mc-case.json未记录材料标签，不能组装模型')
    _materials_spec(materials, required)
    if (any(type(value) is not int for value in (particles, batches, inactive))
            or particles <= 0 or not 0 <= inactive < batches or batches - inactive < 2):
        raise Error('particles须为正整数，batches-inactive至少为2，inactive非负')
    if source_point is not None:
        if (not isinstance(source_point, list) or len(source_point) != 3
                or any(type(item) not in (int, float) or isinstance(item, bool)
                       for item in source_point)):
            raise Error('source_point须为三个数值的列表')
    if seed is not None and (type(seed) is not int or isinstance(seed, bool) or seed < 1):
        raise Error('seed须为正整数或省略')
    value = api('build-dagmc-model', directory,
                {'materials': materials, 'particles': particles, 'batches': batches,
                 'inactive': inactive, 'source_point': source_point, 'seed': seed}, cfg)
    marker['model_built'] = {'materials': value['materials'], 'settings': value['settings'],
                             'xml_files': value['files'], 'professional_acceptance': False}
    atomic_json(marker_path, marker)
    return {'case_dir': case_dir, **value, 'workspace': str(workspace()),
            'location': 'server', 'provenance': provenance(directory, cfg),
            'professional_acceptance': False,
            'note': '模型XML由公开API组装并回读验证；DAGMC材料指派、网格质量、单位换算与'
                    '源位置是否在模型内均需业务确认；未作专业验收。'}
