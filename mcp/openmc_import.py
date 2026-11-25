"""Import existing native inputs; no caller-supplied code is executed."""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import xml.etree.ElementTree as ET

from openmc_runtime import Error, atomic_json, config, file_location, path_in_workspace, relative, workspace
from openmc_jobs import file_hash, list_jobs, is_old_output


def publish_directory(source: Path, target: Path) -> None:
    """Linux atomic, no-replace publication, including a racing empty directory."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, 'renameat2', None)
    if rename is None:
        raise Error('导入目录发布需要Linux renameat2；未发布目标')
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise Error('target_dir已存在，拒绝覆盖')
        raise Error('导入目录发布失败：' + os.strerror(code))


def _binding_map(bindings):
    if bindings is None:return {}
    if not isinstance(bindings, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str)
                                           or not v or not Path(v).is_absolute() for k, v in bindings.items()):
        raise Error('dependency_bindings须为原引用→服务器已有绝对文件路径对象；不自动猜路径或复制核数据库')
    return bindings


def dependency_report(directory: Path, bindings=None, *, apply=False, hash_resources=True) -> dict:
    """One reader for inspection/import; binding writes occur only in import staging."""
    bindings = _binding_map(bindings)
    result, problems, used, mapped = [], [], set(), []
    names = ['model.xml'] if (directory / 'model.xml').exists() else ['geometry.xml', 'materials.xml', 'settings.xml', 'tallies.xml']
    if not (directory / 'model.xml').exists():
        for name in names[:3]:
            if not (directory / name).is_file():problems.append({'file': name, 'message': '缺少原生输入文件：' + name})
    for name in names:
        path = directory / name
        if not path.is_file():continue
        try:
            if path.stat().st_size > config().get('max_input_bytes', 10 * 1024 * 1024):
                raise Error(name + '超过配置的max_input_bytes结构检查上限')
            raw = path.read_bytes()
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise Error(name + '不接受DTD或实体声明')
            try:root = ET.fromstring(raw)
            except ET.ParseError as exc:raise Error(name + ' XML语法错误：' + str(exc)) from exc
            changed = False
            output_paths = {id(c) for parent in root.iter('output') for c in parent if c.tag == 'path'}
            for node in root.iter():
                if id(node) in output_paths:continue
                if node.tag == 'source' and ((node.get('type') or node.findtext('type')) == 'compiled'
                                              or node.get('library') or node.findtext('library')):
                    raise Error('导入暂不执行compiled source；需管理员审查后单独适配')
                if node.tag == 'output':
                    value = node.get('path') or node.findtext('path')
                    if value and value.strip() not in ('.', './'):
                        raise Error('暂不支持非默认settings.output.path；请由模型提供者整理输出路径后导入，未改写原模型')
                refs = []
                if node.tag in ('filename', 'file', 'path', 'library', 'cross_sections') and node.text and node.text.strip():
                    refs.append((node.tag, node.text.strip(), None))
                for attr in ('filename', 'file', 'path', 'library'):
                    if node.tag == 'output' and attr == 'path':continue
                    if node.get(attr):refs.append((attr, node.get(attr), attr))
                for kind, original, attr in refs:
                    value = bindings.get(original, original)
                    if original in bindings:used.add(original)
                    resource = Path(value)
                    info = {'xml_file': name, 'kind': kind, 'reference': original,
                            'effective_reference': value, 'scope': 'server_external' if resource.is_absolute() else 'copied_local',
                            'portable': not resource.is_absolute() and kind != 'cross_sections'}
                    if '..' in resource.parts and not resource.is_absolute():
                        message = name + '引用包含上级路径；请将依赖整理进源目录或显式绑定：' + original
                    else:
                        local = resource if resource.is_absolute() else directory / resource
                        message = None if local.is_file() else (name + ('引用的服务器外部文件不存在：' if resource.is_absolute() else '引用的本地文件缺失：') + value)
                        if message is None:
                            info['bytes'] = local.stat().st_size
                            # Indexes are small; nuclear-data HDF5 is never copied or fully hashed here.
                            if kind == 'cross_sections' and local.suffix.lower() == '.xml':
                                info['index_check'] = check_cross_section_index(local)
                                if info['index_check']['missing_count']:
                                    info['warning'] = '截面索引引用的核数据缺失：' + str(info['index_check']['missing_preview'])
                            if not resource.is_absolute() and hash_resources:info['sha256'] = file_hash(local)
                            if kind == 'cross_sections':
                                info.update(status='index_copied_dependencies_unverified' if not resource.is_absolute() else 'server_reference',
                                            note='核数据不随导入复制；索引检查只核验引用文件存在，不代表核数据适用性。')
                    info['exists'] = message is None
                    result.append(info)
                    if message:problems.append({'file': name, 'reference': original, 'message': message})
                    if original in bindings:
                        mapped.append({'xml_file': name, 'reference': original, 'server_path': value})
                        if apply and message is None:
                            if attr:node.set(attr, value)
                            else:node.text = value
                            changed = True
            if changed:ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)
        except (Error,OSError) as exc:problems.append({'file': name, 'message': str(exc)})
    for key in bindings.keys() - used:problems.append({'reference': key, 'message': '绑定未匹配任何模型引用，未猜测使用位置：' + key})
    return {'dependencies': result, 'problems': problems, 'bindings': mapped, 'ready': not problems and not any(r.get('warning') for r in result),
            'warnings': [r['warning'] for r in result if r.get('warning')]}


def check_cross_section_index(path):
    """Inspect local library references without opening or copying multi-GB HDF5."""
    if path.stat().st_size > config().get('max_input_bytes', 10 * 1024 * 1024):raise Error('截面索引过大，未读取')
    raw = path.read_bytes()
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise Error('截面索引过大或含DTD/实体，未解析：' + str(path))
    try:root = ET.fromstring(raw)
    except ET.ParseError as exc:raise Error('截面索引XML无效：' + str(path)) from exc
    directory = Path(root.findtext('directory') or '.')
    if not directory.is_absolute():directory = path.parent / directory
    files = [directory / n.get('path') for n in root.findall('library') if n.get('path')]
    missing = [str(p) for p in files if not p.is_file()]
    return {'index_path': str(path), 'sha256': file_hash(path), 'libraries': len(files),
            'missing_count': len(missing), 'missing_preview': missing[:20], 'applicability_verified': False}


def input_output_filter(directory):
    """Filename cleanup cannot discard a resource explicitly referenced by XML.

    Preserve matching basenames conservatively across nested directories; the
    same predicate drives copy and disk admission, so neither undercounts it.
    """
    report=dependency_report(directory,hash_resources=False)
    declared={Path(dep['effective_reference']).name for dep in report['dependencies']
              if dep['scope']=='copied_local'}
    return lambda name:is_old_output(name) and name not in declared


def dependencies(directory: Path) -> list[dict]:
    report = dependency_report(directory)
    if report['problems']:raise Error(report['problems'][0]['message'])
    return report['dependencies']


def _source_directory(source_dir):
    source=path_in_workspace(source_dir)
    if not source.is_dir():raise Error('source_dir不是模型目录；请提供含model.xml或materials/geometry/settings.xml的目录')
    if source==workspace():raise Error('source_dir不能是整个工作区；请选择具体模型输入目录')
    for path in source.rglob('*'):
        if path.is_symlink() or not (stat.S_ISREG(path.stat().st_mode) or path.is_dir()):
            raise Error('源模型包含符号链接或特殊文件，未导入：'+path.relative_to(source).as_posix())
        if path.name.startswith('.mc-'):raise Error('源模型包含内部记录目录，先整理纯输入文件后导入')
    return source


def inspect_model(source_dir: str, dependency_bindings=None) -> dict:
    source = _source_directory(source_dir)
    report = dependency_report(source, dependency_bindings)
    from nuclear_data import runtime_env
    cfg=config()
    runtime_index=runtime_env(cfg).get('OPENMC_CROSS_SECTIONS')
    return {'source_dir': source_dir, **report,
            'runtime_data':{'configured_library':cfg.get('nuclear_data_library'),'fallback_cross_sections':runtime_index,
                            'note':'模型显式cross_sections优先；这里仅列运行配置后备，不证明核素/温度适用性'}, 'input_kind': 'model.xml' if (source/'model.xml').exists() else 'split_xml',
            'next': ('用相同dependency_bindings调用openmc_import_model，之后参数发现、预览与校核' if report['ready'] else
                     '补齐problems；外部文件可由用户明确绑定dependency_bindings后重新检查'),
            'professional_acceptance': False,
            'note': '只读检查，未执行脚本、修改文件或复制核数据；ready只表示已声明依赖存在，不是API/语义或可计算保证。'}


def import_model(source_dir: str, target_dir: str, api_check: bool = True, dependency_bindings=None) -> dict:
    """Copy a native model in the service workspace, validate, then atomically publish."""
    from openmc_tools import validate_inputs
    if type(api_check) is not bool:
        raise Error('api_check须为布尔值')
    source = _source_directory(source_dir)
    target = path_in_workspace(target_dir, exists=False)
    if target.exists():raise Error('target_dir已存在；请选择新目录，保留原输入')
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise Error('源与目标目录不能相同或互相嵌套；请选择源目录之外的新target_dir')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='import-', dir=target.parent))
    try:
        # `views/` is generated by the geometry preview tool, not model input.
        # Copying it into an imported model would pollute the input manifest and
        # fingerprint; keep the import set to declared native inputs/resources.
        shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns('views'))
        # Recheck copied resources; importing never trusts a foreign marker's provenance.
        for child in staging.rglob('*'):
            if child.is_symlink():
                raise Error('复制期间出现符号链接，未导入')
        original = staging / 'mc-case.json'
        original_hash = file_hash(original) if original.is_file() else None
        atomic_json(original, {'data_kind': 'user_supplied_unverified', 'template': None,
                              'professional_acceptance': False, 'fine_tuned_model_used': False,
                              'import_source': source_dir, 'original_marker_sha256': original_hash,
                              'note': '已有模型导入；来源及专业物理尚未审定，脚本未执行。'})
        bound = dependency_report(staging, dependency_bindings, apply=True)
        if bound['problems']:raise Error(bound['problems'][0]['message'])
        refs = bound['dependencies']
        marker = json.loads(original.read_text())
        marker['dependency_bindings'] = bound['bindings']
        marker['source_input_hashes'] = {n: file_hash(source/n) for n in ('model.xml','geometry.xml','materials.xml','settings.xml','tallies.xml') if (source/n).is_file()}
        atomic_json(original, marker)
        validation = validate_inputs(relative(staging), api_check=api_check)
        if not validation['structure_valid']:
            raise Error('导入输入检查失败：' + '; '.join(validation['problems']))
        if api_check and validation.get('openmc_api', {}).get('ok') is False:
            api = validation['openmc_api']
            context = api.get('context') or {}
            detail = '；'.join(context.get('removed_inputs') or [])
            raise Error('导入输入的OpenMC API解析失败：' + str(api.get('error'))
                        + (('；定位：' + detail) if detail else '')
                        + '；' + str(context.get('suggestion') or '检查输入与当前OpenMC版本的兼容性'))
        files = [{'name': str(p.relative_to(staging)), 'bytes': p.stat().st_size,
                  'sha256': file_hash(p)} for p in sorted(staging.rglob('*')) if p.is_file()]
        publish_directory(staging, target)
        validation['case_dir'] = target_dir
        return {'case_dir': target_dir, 'source_dir': source_dir, 'files': files,
                'dependencies': refs, 'dependency_bindings': bound['bindings'], 'dependency_warnings': bound['warnings'], 'validation': validation, **file_location(target),
                'provenance': {'data_kind': 'user_supplied_unverified', 'professional_acceptance': False},
                'professional_acceptance': False,
                'note': '已导入且原目录未修改；未执行Python脚本。外部服务器路径需部署时提供；先预览后提交。'}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


BINDINGS_SCHEMA = {'type': 'object', 'additionalProperties': {'type': 'string', 'minLength': 1}, 'description': '用户明确提供的原XML引用→服务器已有绝对文件路径；只改导入副本，不猜路径，不复制核数据库。'}

IMPORT_TOOLS = [
    {'name': 'openmc_inspect_model', 'description': '接入自有原生模型前只读检查XML与依赖；列出缺失文件、外部引用与显式绑定，未复制、执行或改变模型。',
     'inputSchema': {'type': 'object','additionalProperties': False, 'properties': {'source_dir': {'type':'string','minLength':1,'description':'工作区内已有原生输入目录；异机文件先上传'}, 'dependency_bindings': BINDINGS_SCHEMA}, 'required':['source_dir']},
     'outputSchema': {'type':'object'},'function': inspect_model, 'annotations': {'readOnlyHint':True,'idempotentHint':True,'openWorldHint':False}},
    {'name': 'openmc_import_model', 'description': '导入工作区内已有OpenMC模型输入，校验后发布新副本；不要求在例题清单中，不执行脚本，不覆盖原输入。',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'dependency_bindings': BINDINGS_SCHEMA, 'source_dir': {'type': 'string', 'minLength': 1, 'description': 'MC_WORKSPACE内已有原生XML及依赖目录，相对路径；异机文件先用MCP上传ZIP并取source_dir。Python脚本须先自行导出XML。'},
                                    'target_dir': {'type': 'string', 'minLength': 1, 'description': 'MC_WORKSPACE内不存在的新副本相对目录，与源目录不嵌套。'},
                                    'api_check': {'type': 'boolean', 'default': True, 'description': '是否用已配置OpenMC Python API解析模型；省略为true。false仅做结构和依赖检查，不能当作可运行证明。'}},
                     'required': ['source_dir', 'target_dir']},
     'outputSchema': {'type': 'object'}, 'function': import_model},
    {'name': 'openmc_list_jobs', 'description': '按可选任务名称和状态列出持久作业；重名返回所有匹配，后续按job_id操作。',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'include_slurm': {'type':'boolean','default':False,'description':'集群任务接续时设true，另返回离线slurm_submissions（含未取回任务），原jobs不变。只读记录不连集群；用原submission_id查询真实状态。'}, 'name': {'type': 'string', 'minLength': 1, 'maxLength': 120, 'description': '可选中文或其他任务名，精确匹配；可重名，不会自动选择某个作业。'},
                                    'state': {'type': 'string', 'enum': ['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'], 'description': '可选当前状态过滤；省略表示全部状态。'},
                                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50, 'description': '最多返回的作业数，按提交时间倒序；total_matches和truncated说明是否未展示完。'}},
                     'required': []}, 'outputSchema': {'type': 'object'}, 'function': list_jobs,
     'annotations': {'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False}},
]
