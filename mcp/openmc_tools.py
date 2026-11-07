"""Independent OpenMC operations exposed by both the CLI and standard MCP registry."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import openmc_jobs as jobs
import openmc_scores
from openmc_runtime import (ROOT, Error, api, atomic_json, command, config, path_in_workspace,
                            provenance, relative, workspace, file_location)


def environment() -> dict:
    cfg = config()
    result = {'runtime': cfg['runtime'], 'expected_version': cfg['expected_version'],
              'workspace': str(workspace()), 'location': 'server',
              'api_available': False, 'executable_available': False,
              'cross_sections_configured': bool(__import__('os').environ.get('OPENMC_CROSS_SECTIONS')),
              'professional_acceptance': False}
    try:
        probe = api('probe', workspace(), {}, cfg)
        result.update(api_available=True, api_version=probe['api_version'])
    except Error as exc:
        result['api_error'] = str(exc)
    try:
        completed = subprocess.run(command(cfg, workspace(), arguments=['--version']),
                                   capture_output=True, text=True, timeout=30)
        text = (completed.stdout + completed.stderr).strip()
        result['executable_available'] = completed.returncode == 0
        result['executable_version_output'] = text[:1000]
        result['openmc_version_matches'] = completed.returncode == 0 and cfg['expected_version'] in text
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['executable_error'] = type(exc).__name__ + '；请按部署文档配置OpenMC运行时'
        result['openmc_version_matches'] = False
    from nuclear_data import deployment_status
    import runtime_identity
    identity = runtime_identity.snapshot()
    result['package'] = {'version': identity['version'],
                         'code_fingerprint': identity['code_fingerprint'],
                         'source': 'disk_at_call',
                         'note': ('CLI直接读取调用时的磁盘身份；MCP服务另给service启动快照与漂移判断，'
                                  '不能把这里的磁盘版本当成进程已加载版本。')}
    result['cross_sections_env_set'] = result['cross_sections_configured']
    result['nuclear_data'] = deployment_status(cfg)
    result['note'] = ('接口包、可执行程序和核数据分别检查；openmc_version_matches只针对OpenMC；'
                      '探测成功不等于物理验收。')
    return result


def create_model(case_dir: str, template: str = 'synthetic-mgxs', particles: int = 200,
                 batches: int = 10, inactive: int = 2) -> dict:
    if template not in ('synthetic-mgxs', 'pwr-pin-cell', 'pincell-multigroup',
                        'assembly-17x17', 'core-lattice', 'control-rod', 'hex-lattice', 'pincell-depletion'):
        raise Error('当前支持synthetic-mgxs、pwr-pin-cell、pincell-multigroup、'
                    'assembly-17x17、core-lattice、control-rod、hex-lattice、pincell-depletion模板')
    if any(type(v) is not int for v in (particles, batches, inactive)) or particles <= 0 or not 0 <= inactive < batches or batches - inactive < 2:
        raise Error('particles须为正整数，batches-inactive至少为2，inactive非负')
    directory = path_in_workspace(case_dir, exists=False)
    if directory.exists():
        raise Error('目标算例目录已存在；请选择新目录，避免覆盖输入')
    directory.mkdir(parents=True)
    try:
        value = api('create', directory, {'template': template, 'particles': particles,
                                        'batches': batches, 'inactive': inactive})
    except Error:
        # Keep any partial products for diagnosis rather than deleting user-visible evidence.
        atomic_json(directory / 'creation-failed.json', {'state': 'failed', 'note': '生成失败，不能当作完整输入使用。'})
        raise
    return {'case_dir': case_dir, **value, 'workspace': str(workspace()),
            'case_server_path': str(directory), 'location': 'server',
            'generation_method': 'deterministic_public_api_template',
            'fine_tuned_model_used': False}


def _semantic_index(cfg: dict, directory: Path | None = None, root=None):
    """按运行时同一解析顺序定位生效的截面索引；返回(路径, 来源)或(None, 原因)。"""
    import os
    # An explicit model index takes precedence, just as ensure_for_model/OpenMC do.
    if root is not None and directory is not None:
        source = root.findtext('.//cross_sections')
        if source:
            path = Path(source)
            path = path if path.is_absolute() else directory / path
            if path.is_file():return path, 'model_materials_cross_sections'
            return None, '模型声明的cross_sections不存在：' + str(path)
    configured = cfg.get('cross_sections')
    if configured:
        path = Path(configured)
        if path.is_file():
            return path, 'deployment_config_cross_sections'
        return None, '部署配置cross_sections不存在：' + str(path)
    env_value = os.environ.get('OPENMC_CROSS_SECTIONS')
    if env_value:
        path = Path(env_value)
        if path.is_file():
            return path, 'env_OPENMC_CROSS_SECTIONS'
        return None, '环境变量OPENMC_CROSS_SECTIONS指向的索引不存在'
    managed = __import__('nuclear_data').managed_directory(cfg) / 'cross_sections.xml'
    if managed.is_file():
        return managed, 'managed_cache'
    key = cfg.get('nuclear_data_library', 'endfb-viii.0')
    pinned = ROOT / ('config/data-index/' + key + '.xml')
    if pinned.is_file():
        return pinned, 'pinned_data_index'
    return None, '未找到截面索引；先初始化核数据或在部署配置cross_sections'


# 以下合法值集合取自安装版 OpenMC 0.16.0 源码内省（.runtime/openmc），非凭记忆：
# filter 类型 = openmc/filter.py _FILTER_TYPES；密度单位 = openmc/material.py DENSITY_UNITS。
KNOWN_FILTER_TYPES = (
    'universe', 'material', 'cell', 'cellborn', 'surface', 'mesh', 'energy',
    'energyout', 'mu', 'musurface', 'polar', 'azimuthal', 'distribcell',
    'delayedgroup', 'energyfunction', 'cellfrom', 'materialfrom', 'legendre',
    'spatiallegendre', 'sphericalharmonics', 'zernike', 'zernikeradial',
    'particle', 'particleproduction', 'cellinstance', 'collision', 'time',
    'parentnuclide', 'weight', 'meshborn', 'meshsurface', 'meshmaterial',
    'reaction')
DENSITY_UNITS = ('g/cm3', 'g/cc', 'kg/m3', 'atom/b-cm', 'atom/cm3', 'sum', 'macro')
ID_FILTER_TYPES = frozenset(('cell', 'cellborn', 'cellfrom', 'surface', 'material',
                             'materialfrom', 'universe', 'distribcell', 'mesh',
                             'meshborn', 'meshmaterial', 'meshsurface', 'delayedgroup'))
INCREASING_FILTER_TYPES = frozenset(('energy', 'energyout', 'time', 'azimuthal', 'musurface'))
BOUNDED_FILTER_TYPES = {'mu': (-1.0, 1.0), 'polar': (0.0, 3.141592653589793)}
NO_BINS_FILTER_TYPES = frozenset(('energyfunction',))


def _check_filter(tally_label: str, element, problems: list) -> None:
    """单个 tally filter 的静态规则：类型注册表、bins 存在性与数值规则。"""
    ftype = (element.get('type') or '').strip()
    location = f'{tally_label} type={ftype or "(缺失)"}'
    if not ftype:
        problems.append({'kind': 'filter_type', 'severity': 'error', 'location': location,
                         'detail': 'filter缺type属性'})
        return
    if ftype not in KNOWN_FILTER_TYPES:
        problems.append({'kind': 'filter_type', 'severity': 'error', 'location': location,
                         'detail': '未知filter类型；对照安装版OpenMC 0.16.0注册表，权威判定用api_check引擎解析'})
        return
    if ftype in NO_BINS_FILTER_TYPES:
        return
    bins_text = element.findtext('bins')
    if bins_text is None or not bins_text.split():
        problems.append({'kind': 'filter_bins', 'severity': 'error', 'location': location,
                         'detail': 'bins缺失或为空'})
        return
    tokens = bins_text.split()
    if ftype in ID_FILTER_TYPES:
        for token in tokens:
            try:
                int(token)
            except ValueError:
                problems.append({'kind': 'filter_bins_values', 'severity': 'error',
                                 'location': location,
                                 'detail': f'bins应为整数id，出现{token!r}'})
                return
    elif ftype in INCREASING_FILTER_TYPES or ftype in BOUNDED_FILTER_TYPES:
        values = []
        for token in tokens:
            try:
                values.append(float(token))
            except ValueError:
                problems.append({'kind': 'filter_bins_values', 'severity': 'error',
                                 'location': location,
                                 'detail': f'bins应为数值，出现{token!r}'})
                return
        if any(v != v or v in (float('inf'), float('-inf')) for v in values):
            problems.append({'kind': 'filter_bins_values', 'severity': 'error',
                             'location': location, 'detail': 'bins含NaN/inf等非有限值'})
            return
        if any(b <= a for a, b in zip(values, values[1:])):
            problems.append({'kind': 'filter_bins_values', 'severity': 'error',
                             'location': location, 'detail': 'bins须严格单调递增'})
            return
        bounds = BOUNDED_FILTER_TYPES.get(ftype)
        if bounds and (values[0] < bounds[0] or values[-1] > bounds[1]):
            problems.append({'kind': 'filter_bins_values', 'severity': 'error',
                             'location': location,
                             'detail': f'bins超出[{bounds[0]}, {bounds[1]}]范围'})
    # 其余类型（particle/reaction/collision等）仅要求bins非空；深层语义以api_check为准


def _check_materials(root, problems: list) -> int:
    """连续能材料静态规则：密度正定与单位合法、温度正定。返回检查的材料数。"""
    count = 0
    for material in root.iter('material'):
        count += 1
        label = material.get('name') or ('id=' + (material.get('id') or '?'))
        temperature = material.get('temperature')
        if temperature is not None:
            try:
                if not math.isfinite(float(temperature)) or not float(temperature) > 0:
                    raise ValueError
            except ValueError:
                problems.append({'kind': 'temperature', 'severity': 'error',
                                 'location': f'material {label}',
                                 'detail': f'temperature属性{temperature!r}应为正的开尔文值'})
        if material.get('density') is not None:
            problems.append({'kind': 'removed_density_attribute', 'severity': 'error',
                             'location': f'material {label} density',
                             'detail': 'OpenMC 0.16 已移除 density 属性；改用 '
                                       '<density value="..." units="..."/> 子元素。'})
            continue
        if material.find('macroscopic') is not None:
            continue
        densities = material.findall('density')
        if not densities:
            problems.append({'kind': 'density_value', 'severity': 'error',
                             'location': f'material {label}',
                             'detail': '未给出density；引擎装载将拒绝'})
            continue
        density = densities[0]
        units = (density.get('units') or '').strip()
        raw = density.get('value')
        if units not in DENSITY_UNITS:
            problems.append({'kind': 'density_units', 'severity': 'error',
                             'location': f'material {label} density',
                             'detail': f'units{units!r}不在0.16.0合法集{"/".join(DENSITY_UNITS)}'})
            continue
        if units == 'sum':
            if raw is not None:
                problems.append({'kind': 'density_value', 'severity': 'warning',
                                 'location': f'material {label} density',
                                 'detail': 'units=sum时value被引擎忽略（与引擎告警行为一致）'})
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            problems.append({'kind': 'density_value', 'severity': 'error',
                             'location': f'material {label} density',
                             'detail': f'value{raw!r}缺失或不能解析为数值'})
            continue
        if not math.isfinite(value) or not value > 0:
            problems.append({'kind': 'density_value', 'severity': 'error',
                             'location': f'material {label} density',
                             'detail': '密度须为正的有限值；引擎0.16.0实测装载即拒绝非正值'
                                       '（Need to specify a positive density）'})
    return count



def _removed_input_problems(path: Path) -> list[str]:
    """Detect input syntax removed by the pinned OpenMC version before parsing.

    OpenMC 0.16 no longer accepts ``<material density="...">``. The SDK raises
    an opaque AttributeError deep in its XML reader, so callers must not have to
    discover this by trial and error.
    """
    if path.name not in ('materials.xml', 'model.xml'):
        return []
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []  # The structure check reports parse errors with the file name.
    problems = []
    for material in root.iter('material'):
        if material.get('density') is None:
            continue
        label = material.get('name') or ('id=' + (material.get('id') or '?'))
        problems.append(
            path.name + ': material ' + label + ' 使用了 OpenMC 0.16 已移除的 density 属性；'
            '请改为子元素 <density value="..." units="..."/>（例如 '
            '<density value="10.0" units="g/cc"/>），不要保留属性写法。')
    return problems


def _api_failure_context(directory: Path) -> dict:
    removed = _removed_input_problems(directory / 'materials.xml')
    if not removed and (directory / 'model.xml').is_file():
        removed = _removed_input_problems(directory / 'model.xml')
    return {'removed_inputs': removed,
            'suggestion': ('先把输入改为当前 OpenMC 版本的写法；density 属性应改为 '
                           '<density value="..." units="..."/> 子元素。') if removed else
                          '检查 materials/geometry XML 是否使用当前 OpenMC 版本支持的写法；'
                          '静态语义检查仍已执行，不能因 API 解析失败而跳过它。'}

def _semantic_summary(directory: Path, inspected: list, cfg: dict) -> dict:
    """静态语义检查v2：材料密度/温度、核素/S(a,b)可用性、tally filter规则。
    统一problems定位清单；complete=false如实标注未执行的检查；引擎解析仍是权威判定。"""
    if 'materials.xml' not in inspected and 'model.xml' not in inspected:
        return {'checked': False, 'skipped_reason': '材料输入缺失或未通过结构检查，未做语义校验'}
    single = directory / 'model.xml'
    try:
        if single.exists():
            root = ET.parse(single).getroot()
            mode = root.findtext('.//energy_mode', 'continuous-energy')
        else:
            root = ET.parse(directory / 'materials.xml').getroot()
            settings = directory / 'settings.xml'
            mode = (ET.parse(settings).getroot().findtext('.//energy_mode', 'continuous-energy')
                    if settings.exists() else 'continuous-energy')
    except (ET.ParseError, OSError) as exc:
        return {'checked': False, 'skipped_reason': '材料/设置输入无法解析：' + str(exc)[:200]}
    problems = []
    multi_group = mode != 'continuous-energy'
    macroscopic = next(root.iter('macroscopic'), None) is not None
    material_section = {'performed': not (multi_group or macroscopic)}
    materials_count = 0
    if material_section['performed']:
        materials_count = _check_materials(root, problems)
    else:
        material_section['skipped_reason'] = ('多群模式材料体系不同，材料级检查未覆盖'
                                              if multi_group else '存在macroscopic（多群）材料')
    tallies_root = root.find('tallies') if single.exists() else None
    tally_count = definition_count = reference_count = score_count = 0
    if not single.exists() and tallies_root is None and 'tallies.xml' in inspected and (directory / 'tallies.xml').is_file():
        try:
            tallies_root = ET.parse(directory / 'tallies.xml').getroot()
        except (ET.ParseError, OSError):
            tallies_root = None   # 结构检查已单独报告
    if tallies_root is not None:
        # 引擎 0.16.0 真实解析（tallies.py Tally.from_xml_element）：
        # 顶层 <filter id type> 为定义；tally 内 <filters>id…</filters>（复数）为整数 id 引用；
        # tally 内单数 <filter> 子元素会被引擎忽略——常见笔误，按缺陷报出。
        defined = set()
        for element in tallies_root.findall('filter'):
            definition_count += 1
            fid = (element.get('id') or '').strip()
            if fid:
                defined.add(fid)
            else:
                problems.append({'kind': 'filter_id', 'severity': 'warning',
                                 'location': f'filter type={element.get("type") or "?"}',
                                 'detail': 'filter定义缺id，无法被tally引用'})
            _check_filter(f'filter id={fid or "?"}', element, problems)
        for tally in tallies_root.iter('tally'):
            tally_count += 1
            label = tally.get('name') or ('id=' + (tally.get('id') or '?'))
            for stray in tally.findall('filter'):
                problems.append({'kind': 'filter_placement', 'severity': 'error',
                                 'location': f'tally {label}',
                                 'detail': 'tally内单数<filter>子元素会被引擎忽略；'
                                           '定义应放tallies顶层并用<filters>（复数）引用'})
            for ref in tally.findall('filters'):
                for token in (ref.text or '').split():
                    reference_count += 1
                    try:
                        int(token)
                    except ValueError:
                        problems.append({'kind': 'filter_reference', 'severity': 'error',
                                         'location': f'tally {label}',
                                         'detail': f'<filters>引用应为整数id，出现{token!r}'})
                        continue
                    if token not in defined:
                        problems.append({'kind': 'filter_reference', 'severity': 'error',
                                         'location': f'tally {label}',
                                         'detail': f'引用的filter id={token}未在tallies顶层定义'})
            tokens = []
            for node in tally.findall('scores'):
                tokens.extend((node.text or '').split())
            if tokens:
                score_count += len(tokens)
                unknown_scores, score_hints = openmc_scores.check(tokens)
                for name in unknown_scores:
                    close = score_hints.get(name) or []
                    problems.append({'kind': 'invalid_score', 'severity': 'error',
                                     'location': f'tally {label}',
                                     'detail': 'score ' + name + ' 不在OpenMC可识别列表'
                                               + ('（最接近：' + '/'.join(close) + '）' if close else '')
                                               + '；引擎读取tallies.xml时会以Unknown reaction name终止'})
    tally_section = {'performed': tallies_root is not None, 'tallies': tally_count,
                     'filter_definitions': definition_count, 'filter_references': reference_count,
                     'scores_checked': score_count, 'score_catalog': openmc_scores.describe()}
    nuclide_section = {'checked': False}
    missing_nuclides = missing_sab = []
    index_source = None
    if not material_section['performed']:
        nuclide_section['skipped_reason'] = material_section.get('skipped_reason', '')
    else:
        nuclides, sabs = [], []
        for material in root.iter('material'):
            label = material.get('name') or ('id=' + (material.get('id') or '?'))
            for node in material.iter('nuclide'):
                nuclides.append((label, node.get('name') or ''))
            for node in material.iter('sab'):
                sabs.append((label, node.get('name') or ''))
        index, source = _semantic_index(cfg, directory, root)
        if index is None:
            nuclide_section['skipped_reason'] = source
        else:
            try:
                catalog = ET.parse(index).getroot()
            except (ET.ParseError, OSError) as exc:
                nuclide_section['skipped_reason'] = '截面索引无法解析：' + str(exc)[:200]
            else:
                index_source = source
                neutron, thermal = set(), set()
                for entry in catalog.iter('library'):
                    names = (entry.get('materials') or '').split()
                    if entry.get('type') == 'thermal':
                        thermal.update(names)
                    elif entry.get('type') == 'neutron':
                        neutron.update(names)
                missing = [{'material': label, 'nuclide': name}
                           for label, name in nuclides if name not in neutron]
                missing_sab_entries = [{'material': label, 'sab': name}
                                       for label, name in sabs if name not in thermal]
                for item in missing:
                    problems.append({'kind': 'missing_nuclide', 'severity': 'error',
                                     'location': f"material {item['material']}",
                                     'detail': f"核素{item['nuclide']}不在生效截面索引"})
                for item in missing_sab_entries:
                    problems.append({'kind': 'missing_sab', 'severity': 'error',
                                     'location': f"material {item['material']}",
                                     'detail': f"S(a,b){item['sab']}不在生效截面索引"})
                missing_nuclides = sorted({i['nuclide'] for i in missing})
                missing_sab = sorted({i['sab'] for i in missing_sab_entries})
                nuclide_section.update(checked=True, index_source=source, index_path=str(index),
                                       nuclides_checked=len({n for _, n in nuclides}),
                                       sab_checked=len({n for _, n in sabs}))
    errors = [p for p in problems if p['severity'] == 'error']
    complete = bool(material_section['performed'] and nuclide_section.get('checked')
                    and (tallies_root is not None or 'tallies.xml' not in inspected))
    result = {'checked': True, 'ok': not errors, 'complete': complete,
              'problems': problems[:100], 'problems_truncated': len(problems) > 100,
              'error_count': len(errors), 'warning_count': len(problems) - len(errors),
              'materials_checked': materials_count, 'material_section': material_section,
              'tally_section': tally_section, 'nuclide_availability': nuclide_section,
              'library_key': cfg.get('nuclear_data_library', 'endfb-viii.0'),
              'note': '静态语义检查：材料密度/温度、核素/S(a,b)可用性、tally filter类型与bins规则，'
                      '统一problems定位清单；complete=false表示部分检查未执行（原因见相应skipped_reason）。'
                      '温度-数据范围对照、filter深层语义以api_check为准；几何重叠属几何检查；通过不代表物理正确。'}
    if nuclide_section.get('checked'):
        result['missing_nuclides'] = missing_nuclides
        result['missing_sab'] = missing_sab
    if index_source:
        result['index_source'] = index_source
    return result


PREVIEW_MAX_FILE_BYTES = 2 * 1024 * 1024
PREVIEW_CONTENT_CHARS = 20000


def _preview_summary(roots: dict) -> dict:
    """轻量结构摘要：只数已解析节点并提取常用设置，不重复完整校验。"""
    model = roots.get('model.xml')
    settings = model if model is not None else roots.get('settings.xml')
    summary = {}
    if model is not None:
        summary['layout'] = 'model.xml单文件'
    if settings is not None:
        for tag in ('run_mode', 'energy_mode', 'particles', 'batches', 'inactive', 'seed',
                    'photon_transport'):
            value = settings.findtext('.//' + tag)
            if value is not None:
                summary[tag] = value.strip()
    materials = model if model is not None else roots.get('materials.xml')
    if materials is not None:
        entries = []
        for material in materials.iter('material'):
            nuclides = [node.get('name') for node in material.iter('nuclide')]
            entries.append({'id': material.get('id'), 'name': material.get('name'),
                            'nuclides': nuclides[:20], 'nuclides_truncated': len(nuclides) > 20,
                            'sab': [node.get('name') for node in material.iter('sab')][:10]})
        summary['materials'] = entries[:20]
        summary['materials_truncated'] = len(entries) > 20
    geometry = model if model is not None else roots.get('geometry.xml')
    if geometry is not None:
        summary['cells'] = len(list(geometry.iter('cell')))
        summary['surfaces'] = len(list(geometry.iter('surface')))
    tallies = model if model is not None else roots.get('tallies.xml')
    if tallies is not None:
        entries = []
        for tally in tallies.iter('tally'):
            scores = (tally.findtext('scores') or '').split()
            entries.append({'id': tally.get('id'), 'name': tally.get('name'), 'scores': scores[:20]})
        if entries:
            summary['tallies'] = entries[:20]
    return summary


def preview_input(case_dir: str) -> dict:
    """提交前看清输入：逐文件截断预览、指纹与结构摘要；只读，不作物理判定。"""
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为目录')
    if (directory / 'mc-case.json').is_symlink():
        # provenance() reads this marker separately, after listing the inputs.
        # Refuse before it could follow a link outside the input directory.
        raise Error('mc-case.json为符号链接；预览拒绝读取，作业也会拒绝提交')
    required = ['model.xml'] if (directory / 'model.xml').exists() else \
               ['materials.xml', 'geometry.xml', 'settings.xml']
    # Match ordinary submit/copytree's per-component exclusion policy. Include
    # markers and relative resources, not only the XML that OpenMC parses.
    names = set(required)
    for path in directory.rglob('*'):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            names.add(name)
        elif path.is_file() and not any(jobs.is_old_output(part) for part in Path(name).parts):
            names.add(name)
    files, problems, roots = [], [], {}
    text_suffixes = {'.xml', '.json', '.py', '.txt', '.yaml', '.yml', '.csv', '.md', '.rst', '.sh'}
    for name in sorted(names):
        path = directory / name
        if path.is_symlink():
            problems.append(name + '为符号链接；预览拒绝读取，作业也会拒绝提交')
            files.append({'name': name, 'present': True, 'symlink': True})
            continue
        if not path.is_file():
            problems.append(name + '缺失')
            files.append({'name': name, 'present': False})
            continue
        size = path.stat().st_size
        entry = {'name': name, 'present': True, 'bytes': size,
                 'sha256': jobs.file_hash(path)}
        if size > PREVIEW_MAX_FILE_BYTES:
            entry.update(content=None, truncated=True, note='超过2MiB预览上限；仅返回大小和SHA256，未返回内容')
        elif path.suffix.lower() not in text_suffixes:
            entry.update(content=None, truncated=False, note='资源文件只列大小和SHA256，不返回二进制或未知格式正文')
        else:
            data = path.read_bytes()
            # Do not attach content from a different revision to this fingerprint.
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                problems.append(name + '在预览期间改变；请重新预览')
                entry.update(content=None, truncated=False, changed_during_preview=True)
                files.append(entry)
                continue
            text = data.decode('utf-8', errors='replace')
            entry.update(content=text[:PREVIEW_CONTENT_CHARS],
                         truncated=len(text) > PREVIEW_CONTENT_CHARS)
            if entry['truncated']:
                entry['note'] = f'内容截断至前{PREVIEW_CONTENT_CHARS}字符；完整文件以服务端为准'
            if path.suffix.lower() == '.xml':
                try:
                    roots[name] = ET.fromstring(data)
                except ET.ParseError as exc:
                    entry['parse_error'] = str(exc)[:200]
        files.append(entry)
    return {'case_dir': case_dir, 'files': files, 'summary': _preview_summary(roots),
            'problems': problems, 'manifest_complete': not problems,
            'input_hashes': {item['name']: item['sha256'] for item in files if item.get('sha256')},
            'manifest_policy': 'ordinary_job_snapshot', 'provenance': provenance(directory),
            'professional_acceptance': False, 'workspace': str(workspace()), 'location': 'server',
            'note': '列出普通作业将复制的算例文件及指纹（不包含外部部署核数据库）；文本截断预览，大文件/资源只列指纹。'
                    '应将完整input_hashes与提交快照双向比较，不只是检查预览中已有的子集；不代表几何、核数据或物理正确。'}


GEOMETRY_CHECK_MAX_BATCHES = 2
GEOMETRY_OVERLAP_ERROR = re.compile(r'Overlapping cells detected:\s*([\d,\s]+?)\s*on universe\s+(\d+)')
GEOMETRY_LOW_CHECKS = re.compile(r'There were (\d+) cells with less than (\d+) overlap checks')
GEOMETRY_SUMMARY_ROW = re.compile(r'^\s*(\d+)\s+(\d+)\s*$')
GEOMETRY_DATA_ATTRIBUTES = ('filename', 'materials', 'path', 'file', 'cross_sections')
# 数据文件引用有两种写法：属性（<library materials="x.h5"/>、<dagmc_universe filename>）
# 与元素文本（<cross_sections>x.h5</cross_sections>）。只扫属性会漏掉多群算例。
GEOMETRY_DATA_ELEMENTS = ('cross_sections', 'library')


def _data_reference(container: Path, text: str, found: list, missing: list):
    text = (text or '').strip()
    if not text or Path(text).is_absolute():
        return
    if (container / text).is_file():
        if text not in found:
            found.append(text)
    elif text.lower().endswith(('.h5', '.hdf5', '.h5m')):
        if text not in missing:
            missing.append(text)


def _referenced_data_files(directory: Path, names: list) -> tuple[list, list]:
    """算例内被XML引用的相对数据文件（多群h5、DAGMC h5m等）。
    几何检查在临时副本上跑引擎，副本少了这些文件，引擎会在读数据阶段失败
    （实测：ERROR: Cross section HDF5 file 'synthetic-mgxs.h5' does not exist），
    而旧实现把这种失败记成 ok=false，看起来像“几何有问题”。"""
    found, missing = [], []
    for name in names:
        try:
            root = ET.parse(directory / name).getroot()
        except (ET.ParseError, OSError):
            continue
        for node in root.iter():
            for attribute, value in node.attrib.items():
                if attribute in GEOMETRY_DATA_ATTRIBUTES:
                    _data_reference(directory, value, found, missing)
            if str(node.tag) in GEOMETRY_DATA_ELEMENTS:
                _data_reference(directory, node.text, found, missing)
    return found, missing


def _geometry_check(directory: Path, cfg: dict) -> dict:
    """在临时副本上以削减批次运行 openmc -g 做概率性重叠检查。
    引擎0.16.0实测：-g 开启cell overlap checking并完整输运，重叠时报
    "Overlapping cells detected: <cells> on universe <n>"（rc 255），
    结束时输出 CELL OVERLAP CHECK SUMMARY 逐单元检查次数。
    概率性检查：通过不能证明无重叠，仅表示给定抽样下未发现。"""
    import tempfile
    import time
    from nuclear_data import runtime_env
    try:
        probe = subprocess.run(command(cfg, directory, arguments=['--version']),
                               capture_output=True, text=True, timeout=30)
        if probe.returncode != 0 or cfg['expected_version'] not in (probe.stdout + probe.stderr):
            return {'checked': False,
                    'skipped_reason': 'OpenMC可执行程序不可用或版本不符；先运行openmc_environment并按部署文档修正'}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'checked': False, 'skipped_reason': 'OpenMC可执行程序探测失败：' + type(exc).__name__}
    names = (['model.xml'] if (directory / 'model.xml').exists()
             else ['materials.xml', 'geometry.xml', 'settings.xml'])
    if (directory / 'tallies.xml').exists():
        names.append('tallies.xml')
    for name in names:
        if (directory / name).is_symlink():
            return {'checked': False, 'skipped_reason': f'{name}是符号链接，几何检查拒绝执行'}
    data_files, missing_data = _referenced_data_files(directory, names)
    if missing_data:
        return {'checked': False,
                'skipped_reason': '算例引用的数据文件缺失，几何检查未启动：' + '、'.join(missing_data),
                'missing_data_files': missing_data,
                'note': '缺数据文件不等于几何有问题；补齐数据后重试。'}
    for name in data_files:
        if (directory / name).is_symlink():
            return {'checked': False, 'skipped_reason': f'{name}是符号链接，几何检查拒绝执行'}
    tmp = Path(tempfile.mkdtemp(prefix='mc-geomcheck-', dir=str(directory.parent)))
    try:
        for name in names + data_files:
            shutil.copy2(directory / name, tmp / name)
        settings_name = 'model.xml' if 'model.xml' in names else 'settings.xml'
        tree = ET.parse(tmp / settings_name)
        root = tree.getroot()
        mode = root.findtext('.//run_mode', 'eigenvalue')
        if mode not in ('eigenvalue', 'fixed source'):
            return {'checked': False,
                    'skipped_reason': f'run_mode={mode}不适用几何检查（重叠检查随输运运行进行）'}
        adjusted = {}
        batches = root.find('.//batches')
        if batches is not None:
            try:
                original = int((batches.text or '').strip())
            except ValueError:
                original = None
            if original is None or original > GEOMETRY_CHECK_MAX_BATCHES:
                adjusted['batches'] = f'{batches.text!r}→{GEOMETRY_CHECK_MAX_BATCHES}'
                batches.text = str(GEOMETRY_CHECK_MAX_BATCHES)
        inactive = root.find('.//inactive')
        if inactive is not None and (inactive.text or '').strip() != '0':
            adjusted['inactive'] = f'{inactive.text!r}→0'
            inactive.text = '0'
        if adjusted:
            tree.write(tmp / settings_name, encoding='unicode', xml_declaration=True)
        arguments = ['--threads', '1', '-g']
        if settings_name == 'model.xml':
            arguments.append('model.xml')
        timeout = cfg.get('geometry_check_timeout_seconds', 120)
        started = time.monotonic()
        try:
            completed = subprocess.run(command(cfg, tmp, arguments=arguments), cwd=str(tmp),
                                       env=runtime_env(cfg), capture_output=True, text=True,
                                       timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            tail = ((exc.stdout or '') if isinstance(exc.stdout, str) else '') + \
                   ((exc.stderr or '') if isinstance(exc.stderr, str) else '')
            return {'checked': True, 'completed': False, 'ok': None,
                    'probabilistic': True, 'does_not_prove_no_overlap': True,
                    'reason': f'几何检查超时（>{timeout}秒）；大算例请改用geometry-debug作业模式提交',
                    'elapsed_seconds': round(time.monotonic() - started, 3),
                    'log_tail': tail[-2000:],
                    'note': '超时不代表几何有问题，仅表示本次检查未完成。'}
        elapsed = round(time.monotonic() - started, 3)
        stdout = completed.stdout or ''
        stderr = completed.stderr or ''
        overlaps = []
        for match in GEOMETRY_OVERLAP_ERROR.finditer(stderr + '\n' + stdout):
            cells = [int(token) for token in re.findall(r'\d+', match.group(1))]
            overlaps.append({'cells': cells, 'universe': int(match.group(2))})
        summary = None
        if 'CELL OVERLAP CHECK SUMMARY' in stdout:
            rows = []
            low = None
            for line in stdout.split('CELL OVERLAP CHECK SUMMARY', 1)[1].splitlines():
                row = GEOMETRY_SUMMARY_ROW.match(line)
                if row:
                    rows.append({'cell': int(row.group(1)), 'checks': int(row.group(2))})
                low_match = GEOMETRY_LOW_CHECKS.search(line)
                if low_match:
                    low = {'cells': int(low_match.group(1)), 'min_checks': int(low_match.group(2))}
            summary = {'cells': rows[:200], 'low_check': low}
        ok = completed.returncode == 0 and not overlaps
        result = {'checked': True, 'completed': True, 'ok': ok,
                  'probabilistic': True, 'does_not_prove_no_overlap': True,
                  'returncode': completed.returncode,
                  'overlapping_cells': overlaps, 'overlap_check_summary': summary,
                  'settings_adjusted': adjusted or None, 'elapsed_seconds': elapsed,
                  'log_tail': (stdout + stderr)[-2000:],
                  'note': 'openmc -g概率性重叠检查（临时副本、批次削减至≤2、inactive归零、单线程）；'
                          '通过不能证明无重叠，仅表示给定抽样下未发现。'}
        if completed.returncode != 0 and not overlaps:
            text = stdout + '\n' + stderr
            data_failure = next((marker for marker in
                                 ('Cross section HDF5 file', 'No macroscopic data',
                                  'Could not find', 'No cross sections found')
                                 if marker in text), None)
            if data_failure:
                result.update(completed=False, ok=None,
                              reason='几何检查未能启动：引擎在读取数据阶段失败（' + data_failure
                                     + '）；这不是几何重叠结论，见log_tail',
                              engine_started=False)
            else:
                result['reason'] = '几何检查非零退出但未解析出重叠明细；见log_tail（可能是其他输入错误）'
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def validate_inputs(case_dir: str, api_check: bool = False, semantic_check: bool = False,
                    geometry_check: bool = False) -> dict:
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为目录')
    names = ['model.xml'] if (directory / 'model.xml').exists() else ['materials.xml', 'geometry.xml', 'settings.xml']
    if (directory / 'tallies.xml').exists():
        names.append('tallies.xml')
    problems = []
    inspected = []
    for name in names:
        path = path_in_workspace(str(Path(case_dir) / name)) if (directory / name).exists() else directory / name
        if not path.is_file():
            problems.append(name + '缺失'); continue
        input_limit = config().get('max_input_bytes') or 10 * 1024 * 1024
        if path.stat().st_size > input_limit:
            problems.append(name + f'超过本工具{input_limit // 1024 // 1024}MiB结构检查上限'); continue
        try:
            data = path.read_bytes()
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('不接受DTD或实体声明')
            root = ET.fromstring(data)
            expected = name.removesuffix('.xml')
            if root.tag != expected:
                raise ValueError('根元素应为' + expected)
            inspected.append(name)
            for removed in _removed_input_problems(path):
                if removed not in problems:
                    problems.append(removed)
            for tag in ('particles', 'batches', 'inactive'):
                node = root.find('.//' + tag)
                if node is not None and (int(node.text or '') < (0 if tag == 'inactive' else 1)):
                    problems.append(name + ': ' + tag + '范围无效')
        except (ET.ParseError, ValueError) as exc:
            problems.append(name + ': ' + str(exc)[:300])
    result = {'case_dir': case_dir, 'structure_valid': not problems, 'inspected': inspected,
              'problems': problems, 'validation_level': 'xml_structure_and_basic_settings',
              'provenance': provenance(directory), 'professional_acceptance': False,
              'note': '仅XML结构及少量运行参数检查；未知业务约束和物理正确性未作判定；'
                      '几何重叠仅在请求geometry_check时做概率性检查。'}
    level = result['validation_level']
    if semantic_check:
        result['semantic'] = _semantic_summary(directory, inspected, config())
        if result['semantic'].get('checked'):
            level += '+static_semantics'
    if api_check and not problems:
        try:
            parsed = api('validate', directory, {})
            result['openmc_api'] = {**parsed, 'ok': True}
        except Exception as exc:
            context = _api_failure_context(directory)
            result['openmc_api'] = {'ok': False, 'api_parsed': False,
                                    'error': type(exc).__name__ + '：' + str(exc), 'context': context,
                                    'note': 'OpenMC API 解析失败；结构检查与（若请求的）静态语义检查仍保留。'
                                            '不要把内部异常名当成唯一诊断。'}
            level = ('xml_structure_and_basic_settings+openmc_api_failed'
                     + ('+static_semantics' if '+static_semantics' in level else ''))
        else:
            level = ('xml_and_openmc_model_parser'
                     + ('+static_semantics' if '+static_semantics' in level else ''))
    if geometry_check:
        if problems:
            result['geometry'] = {'checked': False, 'skipped_reason': '结构检查未通过，未做几何检查'}
        else:
            result['geometry'] = _geometry_check(directory, config())
            if result['geometry'].get('completed'):
                level += '+openmc_geometry_debug'
    result['validation_level'] = level
    return result


def submit_job(case_dir: str, mode: str = 'run', threads: int = 1, name: str | None = None) -> dict:
    validation = validate_inputs(case_dir)
    if not validation['structure_valid']:
        raise Error('输入结构检查失败：' + '; '.join(validation['problems']))
    runtime = environment()
    if not runtime['executable_available'] or not runtime['openmc_version_matches']:
        raise Error('OpenMC可执行程序不可用或版本不符；先调用openmc_environment并按部署文档修正')
    from openmc_model_spec import declarative_integrity
    integrity = declarative_integrity(path_in_workspace(case_dir))
    submitted = jobs.submit(case_dir, mode, threads, name=name, _input_integrity=integrity)
    if integrity is not None:
        submitted['input_integrity'] = integrity
    return submitted


def verified_result(job_id: str):
    status = jobs.status(job_id)
    if status['state'] != 'completed':
        raise Error('作业尚未成功完成；先查询openmc_job_status')
    directory = jobs.job_directory(job_id)
    record = jobs.read(directory)
    workflow=record.get('example_workflow')
    if workflow:
        from official_catalog import definition
        if definition(workflow['example_id'])['workflow']=='notebook':
            raise Error('Notebook可含多次求解，不能默认取最后StatePoint画图或导出报告；用openmc_read_example_results的calculation_id选择原始结果，查看对应Notebook报告。')
    if record['mode'] == 'plot':
        raise Error('绘图作业不提供计算statepoint；请查看作业files中的图像')
    candidates = [directory / 'work' / name for name in record.get('output_files', {})
                  if name.startswith('statepoint.') and name.endswith('.h5') and '/' not in name]
    if not candidates:
        raise Error('作业没有statepoint产物；绘图作业请查看files，计算作业请检查日志')
    def batch(path):
        try:
            return int(path.name.split('.')[1])
        except (IndexError, ValueError):
            raise Error('statepoint文件名不能识别')
    point = max(candidates, key=batch)
    if point.is_symlink():
        raise Error('结果文件不能是符号链接')
    if not point.is_file() or jobs.file_hash(point) != record['output_files'][point.name]:
        raise Error('结果文件缺失或已在计算后改变；不能作为本作业原始产物读取')
    return directory, record, point


ADEQUACY_PREVIEW = 0.01
ADEQUACY_ENGINEERING = 0.001
SOURCE_MIN_ACTIVE = 20
SOURCE_MIN_INACTIVE = 10


def _source_convergence(progress):
    """Cheap source-convergence advisory from batch settings, not a proof.

    The StatePoint sigma only describes the active-batch sample. A run with very
    few active batches or too few inactive batches can carry a systematic bias
    much larger than that sigma. We only downgrade the label to preview; we do
    not refuse to read a completed result or claim an entropy test.
    """
    if not isinstance(progress, dict) or progress.get('run_mode') != 'eigenvalue':
        return {'status': 'not_available',
                'reason': '当前结果没有可用的本征值批次设置；未评估源收敛'}
    total = progress.get('batches_total')
    inactive = progress.get('inactive')
    if (type(total) is not int or type(inactive) is not int
            or total <= 0 or inactive < 0 or inactive >= total):
        return {'status': 'not_available',
                'reason': '批次设置缺失或不可解释；未评估源收敛'}
    active = total - inactive
    low = inactive < SOURCE_MIN_INACTIVE or active < SOURCE_MIN_ACTIVE
    result = {'status': 'heuristic_low_inactive' if low else 'heuristic_ok',
              'batches_total': total, 'inactive': inactive, 'active_batches': active,
              'thresholds': {'min_active_batches': SOURCE_MIN_ACTIVE,
                             'min_inactive_batches': SOURCE_MIN_INACTIVE},
              'note': '按inactive/活跃批次数做的低成本提示，不是entropy收敛检验，'
                      '也不证明系统误差已消失。'}
    if low:
        result['downgrade'] = ('inactive过少或活跃批次不足；相对σ不能覆盖源未收敛的'
                               '系统偏差，统计分级降为预览')
    return result


def statistical_adequacy(keff, data, progress=None) -> dict:
    """统计充分性的工程分级（相对σ + 批次设置提示）。
    不是收敛检验：不检测源收敛、欠采样、系统误差，也不能当专业验收。"""
    source = _source_convergence(progress)
    if not isinstance(keff, dict) or keff.get('mean') is None:
        return {'grade': 'not_applicable', 'source_convergence': source,
                'reason': '该作业没有k结果（固定源/绘图作业）或均值不可用'}
    mean, sigma = keff.get('mean'), keff.get('std_dev')
    if sigma is None or isinstance(sigma, bool) or not isinstance(sigma, (int, float)) or sigma != sigma:
        return {'grade': 'unusable', 'mean': mean, 'std_dev': None,
                'source_convergence': source,
                'reason': 'k有均值但无标准差；不能判断统计充分性，也不能用于方案比较',
                'note': '先核对StatePoint的k_combined与日志RESULTS节，再决定是否重算。'}
    relative = abs(float(sigma) / float(mean)) if mean else None
    grade = ('low_statistics_preview' if relative > ADEQUACY_PREVIEW else
             'engineering_estimate' if relative > ADEQUACY_ENGINEERING else 'converged_indicator')
    result = {'grade': grade, 'mean': float(mean), 'std_dev': float(sigma),
              'relative_std_dev': round(relative, 6), 'realizations': data.get('realizations'),
              'source_convergence': source,
              'thresholds': {'low_statistics_preview': '相对σ>1%',
                             'engineering_estimate': '0.1%<相对σ<=1%',
                             'converged_indicator': '相对σ<=0.1%'},
              'note': '按k相对σ的工程分级，只决定“能否当结论引用”；不构成收敛检验，'
                      '不检测源收敛、欠采样与系统误差，也不代表专业验收。'}
    if source.get('status') == 'heuristic_low_inactive' and grade != 'low_statistics_preview':
        result['downgraded_from'] = grade
        result['grade'] = 'low_statistics_preview'
        result['reason'] = source['downgrade']
        result['note'] = ('相对σ看似可用，但批次设置提示源可能未收敛；'
                          '只降级为预览，不拒绝读取结果。' + source['note'])
    return result


def _statepoint_context(directory: Path, record: dict, point: Path) -> dict:
    """标注读到的是哪一个StatePoint：燃耗作业有多个时间步，末步不等于初始状态。"""
    work = directory / 'work'
    present = sorted((p.name for p in work.glob('statepoint.*.h5') if p.is_file()),
                     key=lambda name: int(name.split('.')[1]) if name.split('.')[1].isdigit() else -1)
    index = present.index(point.name) + 1 if point.name in present else None
    context = {'statepoint': point.name, 'statepoint_index': index, 'statepoints_present': len(present)}
    spec = {}
    marker = directory / 'mc-case.json'
    if marker.is_file():
        try:
            spec = json.loads(marker.read_text(encoding='utf-8')).get('depletion') or {}
        except (OSError, ValueError):
            spec = {}
    if record.get('mode') == 'deplete' or spec:
        context['depletion'] = {
            'steps_configured': len(spec.get('timesteps') or []) if spec else None,
            'step_index': index, 'is_last_step': index == len(present),
            'note': ('燃耗作业有多个时间步StatePoint：本结果只对应第' + str(index) + '个（共'
                     + str(len(present)) + '个），不是初始状态，也不代表整条燃耗序列；'
                     '逐时间步与燃耗深度请用openmc_read_depletion。')}
    elif len(present) > 1:
        context['note'] = ('算例有' + str(len(present)) + '个StatePoint；本结果对应'
                           + point.name + '，不是第一个。')
    return context


def read_results(job_id: str, max_values: int = 1000) -> dict:
    if type(max_values) is not int or not 1 <= max_values <= 10000:
        raise Error('max_values须为1..10000')
    directory, record, point = verified_result(job_id)
    status = jobs.status(job_id)
    cfg = jobs.read(directory)['runtime']
    data = api('results', directory / 'work', {'statepoint': point.name, 'max_values': max_values}, cfg)
    warnings = list(data.get('warnings') or [])
    for tally in data.get('tallies', []):
        if tally.get('preview_policy') == 'mesh_nonzero_first':
            if tally.get('zero_values'):
                warnings.append(f"tally {tally['id']}：正文预览优先非零网格箱，零值箱"
                                f"{tally['zero_values']}个；零估计不等于物理零，完整数据见StatePoint/CSV。")
            if tally.get('values_truncated'):
                warnings.append(f"tally {tally['id']}：网格tally共{tally['total_values']}值，"
                                f"预览{len(tally.get('mean', []))}值；preview_indices给出实际序号，"
                                '未展示的值不等于0。')
    data['warnings'] = warnings
    data.update(job_id=job_id, provenance=status['provenance'],
                statepoint=relative(point), statepoint_sha256=hashlib.sha256(point.read_bytes()).hexdigest(),
                statepoint_context=_statepoint_context(directory, record, point),
                statistical_adequacy=statistical_adequacy(data.get('keff'), data, status.get('progress')),
                professional_acceptance=False)
    atomic_json(directory / 'results.json', data)
    return data


def _cleanup_module():
    """按文件路径加载 scripts/cleanup_artifacts.py：存储口径与清理入口共用同一实现，不复制逻辑。"""
    import importlib.util
    path = ROOT / 'scripts/cleanup_artifacts.py'
    spec = importlib.util.spec_from_file_location('cleanup_artifacts', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def storage_report(older_than_days=None, top: int = 10) -> dict:
    """只读存储报告：内部记录逐类计数/字节、状态分布、最大记录榜；
    可选按 older_than_days 预览可回收量（与 cleanup.sh 同口径）。不删除任何内容。"""
    if type(top) is not int or isinstance(top, bool) or not 1 <= top <= 100:
        raise Error('top须为1..100的整数')
    if older_than_days is not None and (
            type(older_than_days) not in (int, float) or isinstance(older_than_days, bool)
            or not math.isfinite(older_than_days) or older_than_days < 0):
        raise Error('older_than_days须为非负有限天数')
    cleanup = _cleanup_module()
    root = workspace()
    from openmc_storage import disk_usage
    disk = disk_usage(config(), root)
    try:
        records = cleanup.inventory(root)
    except cleanup.CleanupError as exc:
        return {'workspace': str(root), 'ok': False, 'disk_usage': disk,
                'error': '清点内部记录失败：' + str(exc),
                'note': '记录损坏或结构异常时不猜测；人工核对修复后再报。本工具不删除任何内容。'}
    entries = []
    states = {}
    totals = {}
    for (kind, identifier), item in records.items():
        state = item['record'].get('state')
        states[state] = states.get(state, 0) + 1
        try:
            _digest, size = cleanup.fingerprint(item['path'])
        except cleanup.CleanupError as exc:
            entries.append({'kind': kind, 'identifier': identifier, 'state': state,
                            'bytes': None, 'size_error': str(exc)})
            continue
        group = totals.setdefault(kind, {'count': 0, 'bytes': 0})
        group['count'] += 1
        group['bytes'] += size
        entries.append({'kind': kind, 'identifier': identifier, 'state': state, 'bytes': size,
                        'finished_at': item['record'].get('finished_at')})
    sized = [entry for entry in entries if entry['bytes'] is not None]
    result = {'workspace': str(root), 'ok': True, 'records': len(records), 'disk_usage': disk,
              'totals': totals, 'all_bytes': sum(entry['bytes'] for entry in sized),
              'states': states,
              'largest': sorted(sized, key=lambda entry: entry['bytes'], reverse=True)[:top],
              'professional_acceptance': False,
              'note': '只读存储报告；内部记录含输入快照与产物副本。删除只能在维护窗口用'
                      './cleanup.sh预览后加--apply执行，本工具不删除任何内容。'}
    unsized = [entry for entry in entries if entry['bytes'] is None]
    if unsized:
        result['size_errors'] = unsized
    if older_than_days is not None:
        try:
            # 预览用allow_repair_references=True：报告场景不删除，修复关联如实列出即可，
            # 避免整个可回收量预览因存在修复计划而不可见。
            preview = cleanup.plan(root, [], [], older_than_days, allow_repair_references=True)
            result['reclaimable'] = {
                'older_than_days': older_than_days,
                'records': len(preview['entries']), 'bytes': preview['bytes'],
                'skipped': preview['skipped'][:50],
                'skipped_truncated': len(preview['skipped']) > 50,
                'repair_references': preview['repair_references'],
                'note': '预览口径与cleanup.sh一致：仅终态记录，引用保护与修复关联如实列出；'
                        '执行删除须维护窗口cleanup.sh --apply。'}
        except cleanup.CleanupError as exc:
            result['reclaimable'] = {'older_than_days': older_than_days,
                                     'error': '预览失败：' + str(exc)}
    return result


def _tally_comparison(job_a, job_b, max_values):
    """D2 第二刀：经 read_results 读两作业 StatePoint，按 tally id 对齐逐 bin 对比。
    两倍σ判据按两作业独立假设合成，为工程参考，不构成统计显著性判定。"""
    try:
        data_a = read_results(job_a, max_values=max_values)
        data_b = read_results(job_b, max_values=max_values)
    except Error as exc:
        return {'performed': False,
                'reason': '至少一方无法读取StatePoint结果：' + str(exc),
                'note': '两作业都完成且有有效StatePoint才能对比；如实报告，不猜测。'}
    tallies_a = {tally['id']: tally for tally in data_a.get('tallies', [])}
    tallies_b = {tally['id']: tally for tally in data_b.get('tallies', [])}
    entries = []
    for tally_id in sorted(set(tallies_a) & set(tallies_b)):
        one, two = tallies_a[tally_id], tallies_b[tally_id]
        mismatch = [field for field in ('shape', 'scores', 'nuclides', 'estimator')
                    if one.get(field) != two.get(field)]
        if ([item['type'] for item in one.get('filters', [])]
                != [item['type'] for item in two.get('filters', [])]):
            mismatch.append('filters')
        entry = {'id': tally_id, 'name': {'a': one.get('name'), 'b': two.get('name')},
                 'aligned': not mismatch, 'structure_mismatch': mismatch,
                 'truncated': bool(one.get('values_truncated') or two.get('values_truncated'))}
        if mismatch:
            entry['shape'] = {'a': one.get('shape'), 'b': two.get('shape')}
            entry['note'] = '结构不一致，未做逐bin对比；如实报告。'
            entries.append(entry)
            continue
        problem_bins = []
        compared = within = outside = non_finite = zero_counted = 0
        max_abs_diff = None
        for index, (mean_a, std_a, mean_b, std_b) in enumerate(
                zip(one['mean'], one['std_dev'], two['mean'], two['std_dev'])):
            if None in (mean_a, std_a, mean_b, std_b):
                non_finite += 1
                if len(problem_bins) < 50:
                    problem_bins.append({'index': index, 'mean_a': mean_a, 'mean_b': mean_b,
                                         'std_dev_a': std_a, 'std_dev_b': std_b,
                                         'non_finite': True})
                continue
            diff = mean_b - mean_a
            if (mean_a == 0 and std_a == 0) or (mean_b == 0 and std_b == 0):
                zero_counted += 1
                if len(problem_bins) < 50:
                    problem_bins.append({'index': index, 'mean_a': mean_a, 'mean_b': mean_b,
                                         'std_dev_a': std_a, 'std_dev_b': std_b,
                                         'zero_count': True,
                                         'note': '零计数bin：一侧均值为0且σ=0，没有可比较的统计不确定性；'
                                                 '不参与2σ判决（否则任何非零差异都成了“多倍σ”）'})
                continue
            compared += 1
            sigma = (std_a ** 2 + std_b ** 2) ** 0.5
            ok = abs(diff) <= 2 * sigma if sigma > 0 else diff == 0
            within += int(ok)
            outside += int(not ok)
            if max_abs_diff is None or abs(diff) > max_abs_diff:
                max_abs_diff = abs(diff)
            if not ok and len(problem_bins) < 50:
                problem_bins.append({'index': index, 'mean_a': mean_a, 'mean_b': mean_b,
                                     'std_dev_a': std_a, 'std_dev_b': std_b,
                                     'difference': diff, 'combined_std_dev': sigma,
                                     'within_2sigma': False})
        entry.update(bins_compared=compared, within_2sigma=within, outside_2sigma=outside,
                     zero_count_bins=zero_counted,
                     non_finite_bins=non_finite, max_abs_diff=max_abs_diff,
                     problem_bins=problem_bins,
                     problem_bins_truncated=(outside + zero_counted + non_finite) > len(problem_bins))
        entries.append(entry)
    return {'performed': True, 'max_values': max_values, 'by_id': entries,
            'only_in_a': sorted(set(tallies_a) - set(tallies_b)),
            'only_in_b': sorted(set(tallies_b) - set(tallies_a)),
            'summary': {
                'tallies': len(entries),
                'aligned': sum(1 for entry in entries if entry['aligned']),
                'bins_compared': sum(entry.get('bins_compared', 0) for entry in entries),
                'within_2sigma': sum(entry.get('within_2sigma', 0) for entry in entries),
                'outside_2sigma': sum(entry.get('outside_2sigma', 0) for entry in entries),
                'zero_count_bins': sum(entry.get('zero_count_bins', 0) for entry in entries),
                'non_finite_bins': sum(entry.get('non_finite_bins', 0) for entry in entries)},
            'warnings': sorted(set(data_a.get('warnings', [])) | set(data_b.get('warnings', []))),
            'note': '两倍σ判据按两作业独立假设合成（|差值|≤2σ_合成），为工程参考，不构成统计显著性判定；'
                    '零计数bin（一侧均值0且σ=0）单列zero_count_bins且不参与判决；'
                    '截断时仅对比前max_values个bin并如实标记；非有限bin不替换为0；问题bin至多列50条。'
                    '完整数据在各作业results.json。'}


def _statepoint_keff(job_id: str) -> dict | None:
    """经已验证StatePoint（与read_results同一条选择路径）取k，失败返回None。
    日志里的Combined k行可能是 -nan，而StatePoint的k_combined是好的——旧实现只读日志，
    于是两个都“已完成”的作业被拒绝比较k。max_values=2只为取k，不搬运全部tally值。"""
    try:
        directory, record, point = verified_result(job_id)
    except Error:
        return None
    try:
        data = api('results', directory / 'work',
                   {'statepoint': point.name, 'max_values': 2}, record['runtime'])
    except Error:
        return None
    keff = data.get('keff') or None
    if not isinstance(keff, dict) or keff.get('mean') is None:
        return None
    return {'mean': keff['mean'], 'std_dev': keff.get('std_dev'),
            'source': keff.get('source') or 'statepoint:k_combined',
            'statepoint': point.name,
            **({'fallback_from': keff['fallback_from']} if keff.get('fallback_from') else {})}


def compare_jobs(job_a: str, job_b: str, compare_tallies: bool = False,
                 max_values: int = 1000) -> dict:
    """两作业对比：输入指纹、状态、运行设置与Combined k；compare_tallies=true加tally逐bin对比。"""
    if job_a == job_b:
        raise Error('请选择两个不同的作业编号')
    sides = {}
    for label, job_id in (('a', job_a), ('b', job_b)):
        status = jobs.status(job_id)
        directory = jobs.job_directory(job_id)
        record = jobs.read(directory)
        progress = status.get('progress') or {}
        candidates = {'statepoint': _statepoint_keff(job_id),
                      'log_combined': progress.get('keff_combined'),
                      'log_average': progress.get('keff_average')}
        sides[label] = {'job_id': job_id, 'state': status['state'],
                        'mode': record.get('mode'), 'case_dir': record.get('case_dir'),
                        'threads': record.get('threads'),
                        'run_mode': progress.get('run_mode'),
                        'batches_total': progress.get('batches_total'),
                        'inactive': progress.get('inactive'),
                        'elapsed_seconds': progress.get('elapsed_seconds'),
                        'keff_combined': progress.get('keff_combined'),
                        'keff_candidates': {source: value for source, value in candidates.items() if value},
                        'input_files': record.get('input_files', {}),
                        'output_count': len(record.get('output_files', {}))}
    a, b = sides['a'], sides['b']
    names = set(a['input_files']) | set(b['input_files'])
    identical = sorted(n for n in names
                       if a['input_files'].get(n) and a['input_files'].get(n) == b['input_files'].get(n))
    differing = sorted(n for n in names
                       if n in a['input_files'] and n in b['input_files']
                       and a['input_files'][n] != b['input_files'][n])
    def keff_side(label):
        candidates = sides[label]['keff_candidates']
        for source in ('statepoint', 'log_combined', 'log_average'):
            value = candidates.get(source)
            if isinstance(value, dict) and value.get('mean') is not None:
                return {**value, 'source': value.get('source') or source}
        return None

    keff_a, keff_b = keff_side('a'), keff_side('b')
    keff = {'a': keff_a, 'b': keff_b,
            'source': {'a': (keff_a or {}).get('source'), 'b': (keff_b or {}).get('source')}}
    if keff_a and keff_b:
        difference = round(keff_b['mean'] - keff_a['mean'], 6)
        keff['difference'] = difference
        if keff_a.get('std_dev') is not None and keff_b.get('std_dev') is not None:
            combined = (keff_a['std_dev'] ** 2 + keff_b['std_dev'] ** 2) ** 0.5
            keff.update(combined_std_dev=round(combined, 6),
                        within_2sigma_engineering=bool(abs(difference) <= 2 * combined),
                        note='difference=b-a；k优先取StatePoint（日志Combined行仅作fallback，来源见source）；'
                             'combined_std_dev按两作业独立假设合成，仅为工程参考，不构成统计显著性判定。')
        else:
            keff['note'] = ('difference=b-a；至少一方无标准差，因此不给2σ工程判据。来源见source。')
    else:
        keff['note'] = ('至少一方无可用k（未完成、固定源、无StatePoint且日志无RESULTS节，'
                        '或数值异常如NaN）；不作差值。')
    comparison = {
        'inputs': {'identical': identical, 'differing': differing,
                   'only_in_a': sorted(set(a['input_files']) - set(b['input_files'])),
                   'only_in_b': sorted(set(b['input_files']) - set(a['input_files']))},
        'keff_combined': keff,
        'runtime': {field: {'a': a[field], 'b': b[field], 'same': a[field] == b[field]}
                    for field in ('state', 'mode', 'threads', 'run_mode',
                                  'batches_total', 'inactive')}}
    if compare_tallies:
        comparison['tallies'] = _tally_comparison(job_a, job_b, max_values)
    return {'job_a': job_a, 'job_b': job_b, 'sides': sides, 'comparison': comparison,
            'workspace': str(workspace()), 'location': 'server', 'professional_acceptance': False,
            'note': '工程对比：输入指纹、状态、运行设置与Combined k'
                    + ('；tally逐bin对比见comparison.tallies。' if compare_tallies
                       else '；tally逐bin对比未请求，compare_tallies=true可开启。')}


TALLY_FILTER_TYPES = ('energy', 'material', 'particle', 'mesh')
PARTICLE_BINS = ('neutron', 'photon', 'electron', 'positron')


def _tally_filters(value):
    """校验并规范化filters：能量/材料/粒子/网格四类；网格每tally最多一个。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise Error('filters须为列表或省略')
    result = []
    mesh_seen = 0
    for item in value:
        if not isinstance(item, dict):
            raise Error('filters每项须为对象：type加对应参数')
        kind = item.get('type')
        if kind not in TALLY_FILTER_TYPES:
            raise Error('filters.type须为' + '/'.join(TALLY_FILTER_TYPES) + '之一')
        if kind == 'energy':
            bins = item.get('bins')
            if (not isinstance(bins, list) or len(bins) < 2
                    or any(type(v) not in (int, float) or isinstance(v, bool) for v in bins)
                    or any(b <= a for a, b in zip(bins, bins[1:]))):
                raise Error('energy.bins须为至少2个严格递增的数值（eV）')
        elif kind == 'material':
            bins = item.get('bins')
            if not isinstance(bins, list) or not bins or any(type(v) is not int or v <= 0 for v in bins):
                raise Error('material.bins须为至少1个正整数材料编号')
        elif kind == 'particle':
            bins = item.get('bins')
            if not isinstance(bins, list) or not bins or any(v not in PARTICLE_BINS for v in bins):
                raise Error('particle.bins须为' + '/'.join(PARTICLE_BINS) + '之一')
        else:
            mesh_seen += 1
            if mesh_seen > 1:
                raise Error('同一tally仅支持一个mesh filter')
            dimension = item.get('dimension')
            if (not isinstance(dimension, list) or len(dimension) != 3
                    or any(type(v) is not int or v <= 0 for v in dimension)):
                raise Error('mesh.dimension须为三个正整数')
            for key in ('lower_left', 'upper_right'):
                corner = item.get(key)
                if (not isinstance(corner, list) or len(corner) != 3
                        or any(type(v) not in (int, float) or isinstance(v, bool) for v in corner)):
                    raise Error('mesh.' + key + '须为三个数值')
            if any(u <= l for l, u in zip(item['lower_left'], item['upper_right'])):
                raise Error('mesh.upper_right须在每个方向上大于lower_left')
            if 'mesh_id' in item and item['mesh_id'] is not None                     and (type(item['mesh_id']) is not int or item['mesh_id'] <= 0):
                raise Error('mesh.mesh_id须为正整数或省略')
        result.append({'type': kind, **{key: value for key, value in item.items() if key != 'type'}})
    return result


def define_tally(case_dir: str, scores: list, filters: list | None = None,
                 name: str | None = None, tally_id: int | None = None) -> dict:
    """向分离XML或model.xml算例追加tally，保留已有几何、材料和运行设置。"""
    cfg = config()
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为已存在的算例目录')
    if not any((directory / name).is_file() for name in ('geometry.xml', 'model.xml')):
        raise Error('算例缺少geometry.xml或model.xml，不能确认是OpenMC输入目录')
    if (not isinstance(scores, list) or not scores
            or any(not isinstance(item, str) or not item.strip() for item in scores)):
        raise Error('scores须为非空字符串列表，如["flux"]')
    unknown, hints = openmc_scores.check(scores)
    if unknown:
        parts = []
        for name in unknown:
            close = hints.get(name) or []
            parts.append(name + ('（最接近：' + '/'.join(close) + '）' if close else ''))
        raise Error('score不在OpenMC可识别列表：' + '；'.join(parts)
                    + '；合法示例：flux / fission / absorption / heating / (n,gamma)；'
                      '名单来源见openmc_scores.describe()')
    normalised = _tally_filters(filters)
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 80):
        raise Error('name须为非空字符串（不超过80字符）或省略')
    if tally_id is not None and (type(tally_id) is not int or isinstance(tally_id, bool) or tally_id <= 0):
        raise Error('tally_id须为正整数或省略（省略用现有最大编号+1）')
    value = api('add-tally', directory,
                {'scores': scores, 'filters': normalised, 'name': name, 'tally_id': tally_id}, cfg)
    return {'case_dir': case_dir, **value, 'workspace': str(workspace()), 'location': 'server',
            'provenance': provenance(directory, cfg), 'professional_acceptance': False,
            'note': '已写入' + value.get('input_file', 'tallies.xml') +
                    '；需重新提交计算后结果才含该tally；计分口径为OpenMC原始归一化。'}


DEPLETION_UNITS = ('s', 'min', 'h', 'd')


VIEW_BASES = ('xy', 'xz', 'yz')


def view_geometry(case_dir: str, basis: str = 'xy', origin: list | None = None,
                  width: list | None = None, pixels: int = 400, color_by: str = 'material',
                  title: str | None = None) -> dict:
    """出2D几何切片图（横切xy/纵切xz/yz），写入算例views目录。"""
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为已存在的算例目录')
    if basis not in VIEW_BASES:
        raise Error('basis须为xy/xz/yz之一')
    if type(pixels) is not int or not 50 <= pixels <= 1200:
        raise Error('pixels须为50..1200的整数')
    if color_by not in ('material', 'cell'):
        raise Error('color_by须为material或cell')
    if origin is not None:
        if (not isinstance(origin, (list, tuple)) or len(origin) != 3
                or any(type(v) not in (int, float) or not __import__('math').isfinite(v) for v in origin)):
            raise Error('origin须为3个数（单位cm）')
    if width is not None:
        numbers = width if isinstance(width, (list, tuple)) else [width]
        if (not 1 <= len(numbers) <= 2
                or any(type(v) not in (int, float) or not __import__('math').isfinite(v) or v <= 0 for v in numbers)):
            raise Error('width须为正数或两个正数（单位cm）')
    if title is not None and (not isinstance(title, str) or len(title) > 200):
        raise Error('title须为不超过200字符的字符串')
    filename = 'geometry-' + basis + '-' + __import__('uuid').uuid4().hex[:12] + '.png'
    target = path_in_workspace(case_dir + '/views', exists=False)
    target.mkdir(exist_ok=True)
    params = {'basis': basis, 'pixels': pixels, 'color_by': color_by,
              'filename': str(Path('views') / filename)}
    if origin is not None:
        params['origin'] = [float(v) for v in origin]
    if width is not None:
        params['width'] = [float(v) for v in (width if isinstance(width, (list, tuple)) else [width])]
    try:
        value = api('view-geometry', directory, params)
    except Error as error:
        message = str(error)
        if 'does not appear to be bounded' in message or 'not bounded' in message:
            raise Error('该几何在' + basis + '切片平面上无界或无法确定有限包围盒；'
                        '请同时给出origin/width指定查看范围，未修改原算例输入') from None
        raise
    value.pop('image', None)
    return {'case_dir': case_dir, **value, **file_location(target / filename),
            'provenance': provenance(directory, config()), 'professional_acceptance': False,
            'note': '几何切片图仅供查看，不代表输入已通过结构/语义/几何检查，也不含任何计算结果。'}



HEAVY_SYMBOLS = {'U', 'Np', 'Pu', 'Am', 'Cm', 'Bk', 'Cf', 'Es', 'Fm', 'Md', 'No', 'Lr'}
ATOMIC_MASS = {'H1': 1.0078, 'B10': 10.0129, 'B11': 11.0093, 'O16': 15.9949,
               'Fe56': 55.9349, 'Cr52': 51.9405, 'Ni58': 57.9353,
               'Zr90': 89.9047, 'Zr91': 90.9056, 'Zr92': 91.9050, 'Zr94': 93.9063, 'Zr96': 95.9083,
               'Th232': 232.0381, 'U233': 233.0396, 'U234': 234.0410, 'U235': 235.0439,
               'U236': 236.0456, 'U238': 238.0508, 'Np237': 237.0482,
               'Pu238': 238.0496, 'Pu239': 239.0522, 'Pu240': 240.0538, 'Pu241': 241.0568,
               'Pu242': 242.0587, 'Am241': 241.0568, 'Cm244': 244.0627,
               'Xe135': 134.9072, 'Sm149': 148.9172, 'Nd143': 142.9098}
EXTREME_BURNUP_MWD_KG = 200.0
WARN_BURNUP_MWD_KG = 80.0


def _atomic_mass(name: str) -> float | None:
    return ATOMIC_MASS.get(name)


def _heavy_mass_estimate(directory: Path) -> dict:
    """Estimate initial heavy-metal mass from explicit material volume/density.

    This is deliberately an estimate: unknown nuclide masses or unsupported
    density units are reported as unavailable rather than silently assumed.
    """
    path = directory / 'materials.xml'
    if not path.is_file():
        path = directory / 'model.xml'
    if not path.is_file():
        return {'available': False, 'reason': '没有materials.xml/model.xml'}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return {'available': False, 'reason': '材料XML不可解析：' + type(exc).__name__}
    total_g, materials = 0.0, []
    for material in root.iter('material'):
        if material.find('macroscopic') is not None:
            continue
        try:
            volume = float(material.get('volume'))
        except (TypeError, ValueError):
            continue
        density = material.find('density')
        if density is None:
            continue
        try:
            density_value = float(density.get('value'))
        except (TypeError, ValueError):
            continue
        units = (density.get('units') or '').lower()
        entries = []
        for nucleus in material.findall('nuclide'):
            name = nucleus.get('name') or ''
            try:
                fraction = float(nucleus.get('ao') if nucleus.get('ao') is not None else nucleus.get('wo'))
            except (TypeError, ValueError):
                continue
            entries.append((name, fraction, 'ao' if nucleus.get('ao') is not None else 'wo'))
        if units in ('g/cc', 'g/cm3'):
            total_mass = density_value * volume
            if entries:
                weights = []
                for name, fraction, kind in entries:
                    mass = _atomic_mass(name)
                    weights.append((name, fraction if kind == 'wo' else (fraction * mass if mass else 0.0)))
                denominator = sum(weight for _, weight in weights)
                numerator = sum(weight for name, weight in weights
                                if ''.join(ch for ch in name if ch.isalpha()) in HEAVY_SYMBOLS)
                if denominator > 0:
                    heavy_fraction = numerator / denominator
                else:
                    heavy_fraction = 1.0
            else:
                heavy_fraction = 1.0
            heavy_g = total_mass * heavy_fraction
        elif units in ('atom/b-cm', 'atom/cm3'):
            if not entries:
                continue
            scale = 1.0e24 if units == 'atom/b-cm' else 1.0
            weights = []
            for name, fraction, kind in entries:
                mass = _atomic_mass(name)
                if mass is None:
                    continue
                weights.append((name, fraction * mass if kind == 'ao' else fraction * mass))
            denominator = sum(weight for _, weight in weights)
            fraction_sum = sum(fraction for name, fraction, kind in entries
                               if _atomic_mass(name) is not None)
            if denominator <= 0 or fraction_sum <= 0:
                continue
            heavy_weight = sum(weight for name, weight in weights
                               if ''.join(ch for ch in name if ch.isalpha()) in HEAVY_SYMBOLS)
            # atom/b-cm -> atoms/cm3; average atom mass = sum(f*m)/sum(f).
            average_mass_g = denominator / fraction_sum / 6.02214076e23
            heavy_g = density_value * scale * volume * average_mass_g * (heavy_weight / denominator)
        else:
            continue
        if heavy_g > 0:
            total_g += heavy_g
            materials.append({'id': material.get('id'), 'volume_cm3': volume,
                              'density': density_value, 'units': units,
                              'heavy_metal_g': heavy_g})
    return {'available': total_g > 0, 'heavy_metal_g': total_g, 'materials': materials,
            'source': path.name,
            'assumption': '按材料体积、密度和核素份额估算；未做燃耗链或几何体积复核。'}


def _depletion_guard(directory: Path, power_watts: float, timesteps: list,
                     timestep_unit: str, acknowledge: bool) -> dict:
    factor = {'s': 1 / 86400, 'min': 1 / 1440, 'h': 1 / 24, 'd': 1.0}[timestep_unit]
    days = sum(float(v) for v in timesteps) * factor
    estimate = _heavy_mass_estimate(directory)
    if not estimate.get('available'):
        return {'status': 'unavailable', 'reason': estimate.get('reason') or '重金属质量无法估算',
                'note': '不能用体积/密度估算burnup；配置仍写入，但不要把功率当已验证的物理条件。'}
    mass_kg = estimate['heavy_metal_g'] / 1000.0
    burnup = power_watts * days / (1.0e6 * mass_kg) if mass_kg > 0 else None
    value = {'status': 'ok', 'heavy_metal_g': round(estimate['heavy_metal_g'], 6),
             'heavy_metal_kg': round(mass_kg, 9), 'total_days': round(days, 6),
             'estimated_burnup_mwd_kg': round(burnup, 6) if burnup is not None else None,
             'source': estimate.get('source'), 'assumption': estimate.get('assumption')}
    if burnup is not None and burnup > EXTREME_BURNUP_MWD_KG and not acknowledge:
        raise Error('燃耗配置不自洽：按初始重金属质量约' + str(round(estimate['heavy_metal_g'], 3))
                    + ' g、连续' + str(round(days, 3)) + '天、功率' + str(power_watts)
                    + ' W，估算burnup≈' + str(round(burnup, 3)) + ' MWd/kgHM，超过工程上限'
                    + str(EXTREME_BURNUP_MWD_KG) + ' MWd/kgHM。请核对功率/体积/密度/时间步；'
                    '若这是有意的高燃耗设计，传 acknowledge_extreme_burnup=true 后重试，'
                    '该确认会写入mc-case.json。')
    if burnup is not None and burnup > WARN_BURNUP_MWD_KG:
        value['status'] = 'extreme'
        value['warning'] = ('估算burnup≈' + str(round(burnup, 3)) + ' MWd/kgHM，高于常规热堆范围；'
                            '必须核对功率、质量与时间步，不能把配置成功当成物理可行。')
    return value

def configure_depletion(case_dir: str, power_watts: float, timesteps: list,
                        timestep_unit: str = 'd', chain_file: str | None = None,
                        acknowledge_extreme_burnup: bool = False) -> dict:
    """为算例写入燃耗配置（功率、时间步、单位、链文件）；提交时用mode=deplete执行。"""
    cfg = config()
    directory = path_in_workspace(case_dir)
    if not directory.is_dir():
        raise Error('case_dir须为已存在的算例目录')
    if not (directory / 'materials.xml').is_file() and not (directory / 'model.xml').is_file():
        raise Error('算例缺少materials.xml或model.xml，不能配置燃耗')
    if type(power_watts) not in (int, float) or isinstance(power_watts, bool) or power_watts <= 0:
        raise Error('power_watts须为正数（单位W）')
    if (not isinstance(timesteps, list) or not timesteps
            or any(type(v) not in (int, float) or isinstance(v, bool) or v <= 0 for v in timesteps)):
        raise Error('timesteps须为非空正数列表（每步长度）')
    if timestep_unit not in DEPLETION_UNITS:
        raise Error('timestep_unit须为' + '/'.join(DEPLETION_UNITS) + '之一')
    if type(acknowledge_extreme_burnup) is not bool:
        raise Error('acknowledge_extreme_burnup须为布尔值')
    guard = _depletion_guard(directory, float(power_watts), timesteps, timestep_unit,
                             acknowledge_extreme_burnup)
    if chain_file is not None:
        if not isinstance(chain_file, str) or not Path(chain_file).is_file():
            raise Error('chain_file须为存在的文件路径或省略（省略用部署配置的链文件）')
        chain = Path(chain_file)
    else:
        chain = __import__('nuclear_data').chain_file(cfg)
    materials_text = (directory / 'materials.xml').read_text(encoding='utf-8') if (directory / 'materials.xml').is_file() else ''
    model_text = (directory / 'model.xml').read_text(encoding='utf-8') if (directory / 'model.xml').is_file() else ''
    if 'volume=' not in materials_text and 'volume=' not in model_text:
        raise Error('燃耗要求可烧材料显式体积：materials.xml未设volume；用pincell-depletion模板或补体积')
    marker = directory / 'mc-case.json'
    info = json.loads(marker.read_text(encoding='utf-8')) if marker.is_file() else {'data_kind': 'user_supplied_unverified'}
    spec = {'power_watts': float(power_watts), 'timesteps': [float(v) for v in timesteps],
            'timestep_unit': timestep_unit, 'chain_file': str(chain),
            'guard': guard}
    info.update(depletion=spec)
    marker.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    digest = hashlib.sha256(chain.read_bytes()).hexdigest()
    return {'case_dir': case_dir, **spec, 'depletion_guard': guard,
            'chain_sha256': digest, 'steps': len(timesteps),
            'workspace': str(workspace()), 'location': 'server',
            'provenance': provenance(directory, cfg), 'professional_acceptance': False,
            'note': '燃耗配置已写入算例；用openmc_submit_job(mode=deplete)执行，完成后用openmc_read_depletion读取。'}


def read_depletion(job_id: str, key_nuclides: list | None = None) -> dict:
    """读取已完成燃耗作业的depletion_results.h5：逐时间步keff、燃耗估算、关键核素质量。"""
    status = jobs.status(job_id)
    if status['state'] != 'completed':
        raise Error('作业尚未成功完成；先查询openmc_job_status')
    directory = jobs.job_directory(job_id)
    record = jobs.read(directory)
    if record.get('mode') != 'deplete':
        raise Error('该作业不是燃耗作业（mode不是deplete）')
    work = directory / 'work'
    if not (work / 'depletion_results.h5').is_file():
        raise Error('作业缺少depletion_results.h5；确认燃耗已完成')
    if key_nuclides is not None:
        if (not isinstance(key_nuclides, list) or not key_nuclides
                or any(not isinstance(name, str) or not name.strip() for name in key_nuclides)):
            raise Error('key_nuclides须为非空核素名字符串列表或省略')
    value = api('depletion-results', work, {'file': 'depletion_results.h5',
                                            'key_nuclides': key_nuclides}, record['runtime'])
    return {'job_id': job_id, **value, 'workspace': str(workspace()), 'location': 'server',
            'provenance': record['provenance'], 'professional_acceptance': False}


def _report_metadata(directory: Path) -> dict:
    """报告头元数据（表4-1 4.3 五项）：模板版本、参数、OpenMC版本、运行时间、随机种子。
    全部取自作业目录已落盘文件（job.json/work/settings.xml/mc-case.json）；缺失如实写None，不推测。"""
    record = jobs.read(directory)
    provenance_info = record.get('provenance') or {}
    runtime = record.get('runtime') or {}
    marker = {}
    case_path = directory / 'work/mc-case.json'
    if not case_path.is_file():
        case_path = directory / 'mc-case.json'  # Older records used the job root.
    if case_path.is_file():
        try:
            marker = json.loads(case_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            marker = {}
    parameters = {}
    parameter_sources = {}
    settings_path = directory / 'work/model.xml'
    combined_model = settings_path.is_file()
    if not combined_model:
        settings_path = directory / 'work/settings.xml'
    if settings_path.is_file():
        try:
            root = ET.parse(settings_path).getroot()
            if combined_model:
                root = root.find('settings')
        except ET.ParseError:
            root = None
        if root is not None:
            for tag in ('run_mode', 'energy_mode', 'particles', 'batches', 'inactive', 'seed'):
                node = root.find(tag)
                parameters[tag] = node.text if node is not None else None
                if node is not None:parameter_sources[tag] = 'input_xml'
            if (root.find('energy_mode') is None
                    and str(marker.get('openmc_version') or runtime.get('expected_version', '')).startswith('0.16.')):
                parameters['energy_mode'] = 'continuous-energy'
                parameter_sources['energy_mode'] = 'openmc_default'

    def iso(moment):
        if not moment:
            return None
        return datetime.fromtimestamp(moment, tz=timezone.utc).astimezone().isoformat(timespec='seconds')

    started, finished = record.get('started_at'), record.get('finished_at')
    return {'template': provenance_info.get('template'),
            'data_kind': provenance_info.get('data_kind'),
            'generator': marker.get('generator') or marker.get('generation_method'),
            'parameters': parameters, 'parameter_sources': parameter_sources,
            'openmc_version': marker.get('openmc_version') or runtime.get('expected_version'),
            'runtime': {'state': record.get('state'), 'started_at': iso(started),
                        'finished_at': iso(finished),
                        'duration_seconds': round(finished - started, 3) if started and finished else None},
            'seed': parameters.get('seed'),
            'nuclear_data_library': runtime.get('nuclear_data_library'),
            'source_files': {'job': 'job.json',
                             'settings': str(settings_path.relative_to(directory)) if settings_path.is_file() else None,
                             'case': str(case_path.relative_to(directory)) if case_path.is_file() else None}}


def export_report(job_id: str, format: str = 'markdown', tally_id: int | None = None,
                  score: str | None = None, nuclide: str | None = None,
                  filter_selection: list | None = None) -> dict:
    if format not in ('markdown', 'csv', 'pdf', 'doc', 'docx'):
        raise Error('format须为markdown/csv/pdf/docx/doc')
    selection = dict(tally_id=tally_id, score=score, nuclide=nuclide, filter_selection=filter_selection)
    # Presence matters: an explicit [] means select all bins, not job-summary mode.
    if any(value is not None for value in selection.values()):
        from openmc_reports import export_selected_job
        return export_selected_job(job_id, format, selection)
    if format in ('pdf', 'doc', 'docx'):
        from openmc_reports import export_job
        return export_job(job_id, format)
    data = read_results(job_id)
    directory = jobs.job_directory(job_id)
    metadata = _report_metadata(directory)
    note = data['provenance'].get('note', '')
    if format == 'markdown':
        from openmc_visuals import metadata_parameters, template_label
        parameters = metadata_parameters(metadata)
        runtime_info = metadata['runtime']
        duration = f"{runtime_info['duration_seconds']}秒" if runtime_info['duration_seconds'] is not None else '未记录'
        output = '# OpenMC接口联调结果\n\n'
        output += f"作业：{job_id}\n\n数据来源：{data['provenance']['data_kind']}\n\n{note}\n\n"
        output += '## 报告头元数据（表4-1 4.3 五项）\n\n| 项 | 值 |\n|---|---|\n'
        output += f"| 模板版本 | {template_label(metadata)} |\n"
        output += f"| 参数 | {parameters} |\n"
        output += f"| OpenMC版本 | {metadata['openmc_version'] or '未记录'} |\n"
        output += f"| 运行时间 | {runtime_info['started_at'] or '未记录'} → {runtime_info['finished_at'] or '未记录'}（{duration}） |\n"
        output += f"| 随机种子 | {metadata['seed'] or '未记录'} |\n\n"
        output += '**专业验收：未进行。** 数值读取成功不代表满足合同物理指标。\n\n'
        output += '```json\n' + json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n```\n'
        name = 'report.md'
    else:
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['job_id', 'data_kind', 'execution_kind', 'professional_acceptance', 'quantity', 'tally_id', 'scores', 'nuclides', 'flat_index', 'mean', 'std_dev', 'shape', 'values_truncated', 'normalization', 'metadata_file'])
        kind = data['provenance']['data_kind']
        execution_kind = data['provenance']['execution_kind']
        if data['keff']:
            writer.writerow([job_id, kind, execution_kind, False, 'keff', '', '', '', 0, data['keff']['mean'], data['keff']['std_dev'], '[1]', False, 'dimensionless', 'results.json'])
        for tally in data['tallies']:
            for index, (mean, std) in enumerate(zip(tally['mean'], tally['std_dev'])):
                writer.writerow([job_id, kind, execution_kind, False, 'tally_raw', tally['id'], '|'.join(tally['scores']), '|'.join(tally['nuclides']), index, mean, std, json.dumps(tally['shape']), tally['values_truncated'], tally['normalization'], 'results.json'])
        output = stream.getvalue(); name = 'report.csv'
    report = directory / name
    report.write_text(output, encoding='utf-8')
    return {'job_id': job_id, 'format': format, 'selection_mode': 'job_summary', **file_location(report),
            'metadata': metadata, 'provenance': data['provenance'], 'professional_acceptance': False,
            'note': '完整shape、filter及原始计分归一化说明见同作业results.json；截断状态随结果保留。'}
