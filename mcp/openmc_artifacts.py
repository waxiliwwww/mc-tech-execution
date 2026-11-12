"""List local outputs with checksums; registered outputs; MCP transfer lives in file_transfer."""
import mimetypes

import openmc_jobs as jobs
from openmc_runtime import Error, relative, workspace


def artifact_files(kind: str, identifier: str):
    if kind == 'job':
        target = jobs.job_directory(identifier)
        record = jobs.read(target)
    elif kind == 'scan':
        import openmc_scan as scans
        target = scans.directory(identifier)
        record = jobs.read(target)
    else:raise Error('kind须为job或scan')
    paths = []
    for pattern in ('report.md', 'report.csv', 'report.docx', 'report.pdf', 'report.doc', 'results.json',
                    'summary.json', 'summary.csv', 'plot-*.png', 'plot-*.svg', 'plot-*.json', 'plot-*.csv',
                    'scan-*.png', 'scan-*.svg', 'scan-*.json', 'scan-*.csv'):
        paths.extend(target.glob(pattern))
    if kind == 'job':
        for prefix in ('mesh', 'convergence'):
            for suffix in ('png', 'svg', 'json', 'csv'):
                paths.extend(target.glob(f'{prefix}-*.{suffix}'))
    if kind == 'job':
        for name in record.get('output_files', {}):
            path = target / 'work' / name
            if not path.resolve().is_relative_to((target / 'work').resolve()):raise Error('产物记录路径无效')
            paths.append(path)
    return target, record, sorted(set(paths))


def list_artifacts(kind: str, identifier: str) -> dict:
    target, record, paths = artifact_files(kind, identifier)
    from file_transfer import policy
    limits = policy()
    artifacts = []
    for path in sorted(set(paths)):
        if not path.is_file() or path.is_symlink():continue
        digest = jobs.file_hash(path)
        original = record.get('output_files', {}).get(str(path.relative_to(target / 'work'))) if kind == 'job' and path.is_relative_to(target / 'work') else None
        artifacts.append({'path': relative(path), 'server_path': str(path), 'bytes': path.stat().st_size,
                          'sha256': digest, 'mime_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                          'matches_recorded_output': digest == original if original else None,
                          'download_allowed':path.stat().st_size <= limits['max_file_bytes']})
    return {'kind': kind, 'identifier': identifier, 'workspace': str(workspace()),
            'directory': str(target), 'artifacts': artifacts,
            'location': 'server', 'download_supported': True, 'download_transport':'MCP chunks', 'max_file_bytes':limits['max_file_bytes'], 'provenance': record['provenance'],
            'note': '路径位于服务端；需要传输时用openmc_prepare_download；超限大文件留在服务端分析，限额可由管理员调整。不会自动下载或清理。'}
