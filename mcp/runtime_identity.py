"""Process-to-disk identity and drift detection for the MCP service.

The MCP tool list is an in-memory snapshot, while some validators import code
from disk on demand. If the package changes while a process keeps running,
those two halves can disagree and a stale schema looks like a caller error.
This module gives the process one startup identity and lets ``openmc_environment``
compare it with the current package without trusting a value captured at boot.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VERSION_FILE = ROOT / 'VERSION'
IGNORED_PARTS = {'__pycache__', 'logs'}


def code_hashes(base: Path | None = None) -> dict[str, str]:
    """SHA256 of every Python module that can define tools or validation."""
    base = Path(base) if base is not None else ROOT
    package = base / 'mcp'
    result = {}
    for path in sorted(package.rglob('*.py')):
        relative = path.relative_to(base)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def code_fingerprint(hashes: dict[str, str] | None = None) -> str:
    hashes = hashes if hashes is not None else code_hashes()
    digest = hashlib.sha256()
    for name in sorted(hashes):
        digest.update(name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(hashes[name].encode('ascii'))
        digest.update(b'\n')
    return digest.hexdigest()


def version(base: Path | None = None, path: Path | None = None) -> str | None:
    path = Path(path) if path is not None else (Path(base) if base is not None else ROOT) / 'VERSION'
    try:
        return path.read_text(encoding='utf-8').strip() or None
    except OSError:
        return None


def snapshot(base: Path | None = None, version_path: Path | None = None) -> dict:
    hashes = code_hashes(base)
    return {'version': version(base, version_path), 'code_fingerprint': code_fingerprint(hashes),
            'files': len(hashes)}


def check(startup: dict, base: Path | None = None, version_path: Path | None = None) -> dict:
    """Compare a startup snapshot with the package currently on disk."""
    current_hashes = code_hashes(base)
    configured_path = version_path
    if configured_path is None and startup.get('_version_path'):
        configured_path = Path(startup['_version_path'])
    # A literal version in service config has no independent on-disk source;
    # do not invent a second version and call it drift.
    current_version = (startup.get('version') if startup.get('_version_literal')
                       else version(base, configured_path))
    current_fingerprint = code_fingerprint(current_hashes)
    old_hashes = startup.get('_hashes') if isinstance(startup, dict) else None
    if old_hashes is None:
        # A persisted/startup record may contain only the fingerprint. Report
        # the mismatch without pretending to know which files changed.
        changed = None
    else:
        changed = sorted(name for name in set(old_hashes) | set(current_hashes)
                         if old_hashes.get(name) != current_hashes.get(name))
    version_matches = startup.get('version') == current_version
    code_matches = startup.get('code_fingerprint') == current_fingerprint
    restart = not (version_matches and code_matches)
    if restart:
        parts = []
        if not version_matches:
            parts.append('版本 ' + str(startup.get('version')) + ' → ' + str(current_version))
        if not code_matches:
            detail = ('；变更文件：' + '、'.join(changed[:10])) if changed else ''
            parts.append('代码指纹不一致' + detail)
        message = ('服务进程加载的是启动时快照，磁盘代码/版本已经变化（'
                   + '；'.join(parts) + '）。请重启 MCP 服务后再判断参数或结果；'
                   '工具清单与按需导入的校验器可能不是同一版本。')
    else:
        message = ''
    return {'startup_version': startup.get('version'), 'current_version': current_version,
            'version_matches': version_matches,
            'startup_code_fingerprint': startup.get('code_fingerprint'),
            'current_code_fingerprint': current_fingerprint,
            'code_matches': code_matches, 'changed_files': changed,
            'restart_required': restart, 'message': message}


def startup(base: Path | None = None, version_path: Path | None = None,
            literal_version: str | None = None) -> dict:
    """Snapshot plus the hashes and version source needed for a precise later diff."""
    value = snapshot(base, version_path)
    value['_hashes'] = code_hashes(base)
    if version_path is not None:
        value['_version_path'] = str(version_path)
    if literal_version is not None:
        value['version'] = literal_version
        value['_version_literal'] = True
    return value
