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

from openmc_runtime import Error, atomic_json, config, file_location, path_in_workspace, relative
from openmc_jobs import file_hash, list_jobs


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


def dependencies(directory: Path) -> list[dict]:
    """Check declared local resources; external paths remain explicit server conditions."""
    result = []
    names = ['model.xml'] if (directory / 'model.xml').exists() else ['geometry.xml', 'materials.xml', 'settings.xml', 'tallies.xml']
    for name in names:
        path = directory / name
        if not path.is_file():
            continue
        if path.stat().st_size > (config().get("max_input_bytes") or 10 * 1024 * 1024):
            raise Error(name + "超过配置的max_input_bytes结构检查上限")
        raw = path.read_bytes()
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise Error(name + '不接受DTD或实体声明')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise Error(name + ' XML语法错误：' + str(exc)) from exc
        output_paths = {id(child) for parent in root.iter('output') for child in parent if child.tag == 'path'}
        for node in root.iter():
            if id(node) in output_paths:
                continue
            if node.tag == 'source' and ((node.get('type') or node.findtext('type')) == 'compiled'
                                          or node.get('library') or node.findtext('library')):
                raise Error('导入暂不执行compiled source；需管理员审查后单独适配')
            if node.tag == 'output':
                output_path = node.get('path') or node.findtext('path')
                if output_path and output_path.strip() not in ('.', './'):
                    raise Error('暂不支持非默认settings.output.path；请由模型提供者整理输出路径后导入，未改写原模型')
            references = []
            if node.tag in ('filename', 'file', 'path', 'library') and node.text and node.text.strip():
                references.append((node.tag, node.text.strip()))
            if node.tag == 'cross_sections' and node.text and node.text.strip():
                references.append(('cross_sections', node.text.strip()))
            for attribute in ('filename', 'file', 'path', 'library'):
                if node.tag == 'output' and attribute == 'path':
                    continue  # Output directory was checked above; it is not an input file.
                if node.get(attribute):
                    references.append((attribute, node.get(attribute)))
            for kind, value in references:
                resource = Path(value)
                if resource.is_absolute():
                    if not resource.is_file():
                        raise Error(name + '引用的服务器外部文件不存在：' + value)
                    result.append({'xml_file': name, 'kind': kind, 'reference': value,
                                   'scope': 'server_external', 'portable': False})
                else:
                    if '..' in resource.parts:
                        raise Error(name + '引用包含上级路径；请将依赖整理进源目录：' + value)
                    local = directory / resource
                    if not local.is_file():
                        raise Error(name + '引用的本地文件缺失：' + value)
                    result.append({'xml_file': name, 'kind': kind, 'reference': value,
                                   'scope': 'copied_local', 'portable': kind != 'cross_sections',
                                   'sha256': file_hash(local),
                                   **({'status': 'index_copied_dependencies_unverified',
                                       'note': '仅复制截面索引；directory/library引用的核数据文件未递归核验或复制，不能据此判为可搬移。'}
                                      if kind == 'cross_sections' else {})})
    return result


def import_model(source_dir: str, target_dir: str, api_check: bool = True) -> dict:
    """Copy a native model in the service workspace, validate, then atomically publish."""
    from openmc_tools import validate_inputs
    if type(api_check) is not bool:
        raise Error('api_check须为布尔值')
    source = path_in_workspace(source_dir)
    target = path_in_workspace(target_dir, exists=False)
    if (not source.is_dir() or source == path_in_workspace('.', exists=True)
            or target.exists() or target == source or target.is_relative_to(source)
            or source.is_relative_to(target)):
        raise Error('源须为具体模型目录，目标须为不存在且与源无嵌套的目录')
    for path in source.rglob('*'):
        if path.is_symlink() or not (stat.S_ISREG(path.stat().st_mode) or path.is_dir()):
            raise Error('源模型包含符号链接或特殊文件，未导入：' + path.relative_to(source).as_posix())
        if path.name.startswith('.mc-'):
            raise Error('源模型包含内部记录目录，先整理纯输入文件后导入')
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
        refs = dependencies(staging)
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
                'dependencies': refs, 'validation': validation, **file_location(target),
                'provenance': {'data_kind': 'user_supplied_unverified', 'professional_acceptance': False},
                'professional_acceptance': False,
                'note': '已导入且原目录未修改；未执行Python脚本。外部服务器路径需部署时提供；先预览后提交。'}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


IMPORT_TOOLS = [
    {'name': 'openmc_import_model', 'description': '导入工作区内已有OpenMC模型输入，校验后发布新副本；不要求在例题清单中，不执行脚本，不覆盖原输入。',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'source_dir': {'type': 'string', 'minLength': 1, 'description': 'MC_WORKSPACE内已有原生XML及依赖目录，相对路径；Python脚本须先自行导出XML。'},
                                    'target_dir': {'type': 'string', 'minLength': 1, 'description': 'MC_WORKSPACE内不存在的新副本相对目录，与源目录不嵌套。'},
                                    'api_check': {'type': 'boolean', 'default': True, 'description': '是否用已配置OpenMC Python API解析模型；省略为true。false仅做结构和依赖检查，不能当作可运行证明。'}},
                     'required': ['source_dir', 'target_dir']},
     'outputSchema': {'type': 'object'}, 'function': import_model},
    {'name': 'openmc_list_jobs', 'description': '按可选任务名称和状态列出持久作业；重名返回所有匹配，后续按job_id操作。',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'properties': {'name': {'type': 'string', 'minLength': 1, 'maxLength': 120, 'description': '可选中文或其他任务名，精确匹配；可重名，不会自动选择某个作业。'},
                                    'state': {'type': 'string', 'enum': ['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'], 'description': '可选当前状态过滤；省略表示全部状态。'},
                                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50, 'description': '最多返回的作业数，按提交时间倒序；total_matches和truncated说明是否未展示完。'}},
                     'required': []}, 'outputSchema': {'type': 'object'}, 'function': list_jobs,
     'annotations': {'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False}},
]
