"""Persist execution inputs and external-data identity without hashing a nuclear-data archive.

Small index SHA256 + referenced file size/mtime/inode detect ordinary deployment
changes; this is explicitly not a byte-for-byte nuclear-data attestation.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from openmc_runtime import config


def _metadata(path):
    try:
        s = path.stat()
        return {'path': str(path), 'exists': path.is_file(), 'bytes': s.st_size,
                'mtime_ns': s.st_mtime_ns, 'inode': s.st_ino, 'device': s.st_dev}
    except OSError:return {'path':str(path), 'exists':False}


def model_context(directory, cfg=None, input_hashes=None):
    from openmc_import import dependency_report, input_output_filter
    from openmc_jobs import file_hash, is_old_output
    from nuclear_data import runtime_env
    cfg = cfg or config()
    skip=input_output_filter(directory)
    inputs = input_hashes if input_hashes is not None else {str(p.relative_to(directory)):file_hash(p) for p in sorted(directory.rglob('*'))
              if p.is_file() and not any(skip(n) for n in p.relative_to(directory).parts)}
    report = dependency_report(directory,hash_resources=False)
    external = []
    for dep in report['dependencies']:
        if dep['scope'] == 'server_external':external.append(_metadata(Path(dep['effective_reference'])))
    model_file = directory / ('model.xml' if (directory/'model.xml').is_file() else 'materials.xml')
    settings_file = directory / ('model.xml' if (directory/'model.xml').is_file() else 'settings.xml')
    try:
        root = ET.parse(model_file).getroot()
        explicit = root.findtext('.//cross_sections')
        mode = ET.parse(settings_file).getroot().findtext('.//energy_mode') or 'continuous-energy'
        if root.tag == 'materials':explicit = root.findtext('cross_sections')
    except (OSError, ET.ParseError):
        explicit = None; mode = None
    env = runtime_env(cfg)
    reference = explicit or (env.get('OPENMC_MG_CROSS_SECTIONS') if mode == 'multi-group' else env.get('OPENMC_CROSS_SECTIONS'))
    nuclear = {'energy_mode':mode, 'library':cfg.get('nuclear_data_library') if mode=='continuous-energy' else None, 'index':None,
               'metadata_complete':False, 'content_verified':False}
    if reference:
        path = Path(reference)
        if not path.is_absolute():path = directory/path
        nuclear['index'] = _metadata(path)
        if path.is_file():
            nuclear['index']['sha256'] = file_hash(path)
            if path.suffix.lower() == '.xml':
                if path.stat().st_size>cfg.get('max_input_bytes',10*1024*1024):raise ValueError('核数据索引过大，未读取')
                raw = path.read_bytes()
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                    raise ValueError('核数据索引过大或含DTD/实体，不能记录续算身份')
                index = ET.fromstring(raw)
                base = Path(index.findtext('directory') or '.')
                if not base.is_absolute():base = path.parent/base
                libraries = [_metadata(base/node.get('path')) for node in index.findall('library') if node.get('path')]
                nuclear['libraries'] = libraries
                nuclear['metadata_complete'] = bool(libraries) and all(p['exists'] for p in libraries)
            else:
                nuclear['metadata_complete'] = True
    commands = {}
    for key in ('python_command','openmc_command'):
        command = cfg.get(key) or []
        target = shutil.which(command[0], path=env.get('PATH')) if command else None
        commands[key] = {'argv':command, 'executable':_metadata(Path(target)) if target else None}
    return {'input_hashes':inputs, 'external_dependencies':external, 'nuclear_data':nuclear,
            'runtime_sha256':hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest(),
            'commands':commands,
            'dependencies_ready':report['ready'],
            'identity_note':'输入/索引为SHA256；外部HDF5按路径、大小、mtime、inode核对，未全库内容校验；不证明专业适用性。'}


def comparison_context(directory, cfg, input_hashes=None):
    """Submission records only; failures stay explicit without changing legacy jobs."""
    try:return {'checked':True, **model_context(directory,cfg,input_hashes)}
    except (OSError, ValueError, KeyError) as exc:return {'checked':False,'reason':str(exc)[:600]}
