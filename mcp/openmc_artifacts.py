"""List local outputs with checksums; no network download or arbitrary file reads."""
import mimetypes

import openmc_jobs as jobs
from openmc_runtime import Error, relative, workspace


def list_artifacts(kind: str, identifier: str) -> dict:
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
        for name in record.get('output_files', {}):
            path = target / 'work' / name
            if not path.resolve().is_relative_to((target / 'work').resolve()):raise Error('产物记录路径无效')
            paths.append(path)
    artifacts = []
    for path in sorted(set(paths)):
        if not path.is_file() or path.is_symlink():continue
        digest = jobs.file_hash(path)
        original = record.get('output_files', {}).get(str(path.relative_to(target / 'work'))) if kind == 'job' and path.is_relative_to(target / 'work') else None
        artifacts.append({'path': relative(path), 'server_path': str(path), 'bytes': path.stat().st_size,
                          'sha256': digest, 'mime_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                          'matches_recorded_output': digest == original if original else None})
    return {'kind': kind, 'identifier': identifier, 'workspace': str(workspace()), 'artifacts': artifacts,
            'location': 'server', 'download_supported': False, 'provenance': record['provenance'],
            'note': '路径位于服务端；本轮不提供远程下载。输入与大型核数据不会自动清理，请按部署环境管理磁盘。'}
