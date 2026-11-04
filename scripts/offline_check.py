#!/usr/bin/env python3
"""离线/内网部署演练的可查部分：完整性与静态依赖检查（H1 准备件）。

能在本机查实的都查：vendor 例题与 knowledge 官方资料的清单存在性+sha256、
依赖清单完整性与钉版、运行期代码里的网络调用点静态扫描、运行时版本登记。

**不能查实的必须说清**：占位网络（unshare）在本机不被允许，断网实跑与离线
安装只能在目标环境执行；本工具不宣称离线已通过，只输出可查项结论与待演练项。
退出码：0=可查项全过；1=有完整性/依赖问题（如清单缺失、sha256不符、有未钉版依赖）。
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NETWORK_PATTERN = re.compile(r'(import\s+(requests|httpx)\b|urllib\.request|socket\.|'
                             r'https?://[A-Za-z0-9])')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_manifest(path: Path, label: str, problems: list) -> dict:
    if not path.is_file():
        problems.append(label + '清单缺失：' + str(path))
        return {'manifest': str(path.relative_to(ROOT)), 'ok': False}
    data = json.loads(path.read_text(encoding='utf-8'))
    files = data.get('files') or []
    missing, mismatched = [], []
    for entry in files:
        target = path.parent / entry['file']
        if not target.is_file():
            missing.append(entry['file'])
        elif sha256(target) != entry.get('sha256'):
            mismatched.append(entry['file'])
    if missing:
        problems.append(label + '文件缺失：' + ', '.join(missing))
    if mismatched:
        problems.append(label + 'sha256不符：' + ', '.join(mismatched))
    return {'manifest': str(path.relative_to(ROOT)), 'files': len(files),
            'missing': missing, 'mismatched': mismatched,
            'ok': not missing and not mismatched}


def check_requirements(problems: list) -> dict:
    path = ROOT / 'mcp/requirements.txt'
    if not path.is_file():
        problems.append('依赖清单缺失：mcp/requirements.txt')
        return {'ok': False}
    lines = [line.strip() for line in path.read_text().splitlines()
             if line.strip() and not line.strip().startswith('#')]
    unpinned = [line for line in lines if '==' not in line]
    if unpinned:
        problems.append('未钉版依赖：' + ', '.join(unpinned))
    return {'path': 'mcp/requirements.txt', 'sha256': sha256(path),
            'count': len(lines), 'unpinned': unpinned, 'ok': not unpinned}


def scan_network_points() -> dict:
    hits = []
    for folder in ('mcp', 'scripts'):
        for source in sorted((ROOT / folder).glob('*.py')):
            for number, line in enumerate(source.read_text(encoding='utf-8').splitlines(), 1):
                if NETWORK_PATTERN.search(line):
                    hits.append({'file': str(source.relative_to(ROOT)), 'line': number,
                                 'text': line.strip()[:120]})
    return {'hits': hits, 'note': '静态扫描仅列出可疑调用点，不证明运行期无网络依赖；'
                                  '真正的证明是断网实跑（见演练清单）。'}


def runtime_versions() -> dict:
    config = ROOT / 'config/openmc.local.json'
    result = {'config': str(config.relative_to(ROOT)) if config.is_file() else None}
    if not config.is_file():
        return result
    try:
        command = json.loads(config.read_text()).get('python_command') or []
        result['python_command'] = command
        if command:
            probe = subprocess.run([command[0], '-c',
                                    'import sys, openmc; print(sys.version.split()[0]); '
                                    'print(openmc.__version__)'],
                                   capture_output=True, text=True, timeout=120)
            lines = (probe.stdout or '').split()
            if lines:
                result['python'] = lines[0]
                result['openmc'] = lines[1] if len(lines) > 1 else None
            else:
                result['openmc_error'] = (probe.stderr or '').strip()[-200:]
    except (OSError, ValueError) as exc:
        result['openmc_error'] = str(exc)
    return result


def main() -> int:
    problems = []
    report = {
        'generated_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%S'),
        'checker': 'scripts/offline_check.py',
        'vendor_examples': check_manifest(ROOT / 'vendor/openmc-examples/0.16.0/manifest.json',
                                          'vendor例题', problems),
        'knowledge': check_manifest(ROOT / 'knowledge/openmc-0.16.0/manifest.json',
                                    '官方资料', problems),
        'requirements': check_requirements(problems),
        'network_static': scan_network_points(),
        'runtime': runtime_versions(),
        'problems': problems,
        'not_verified': ['离线安装（pip --no-index）未在本机验证',
                         '断网实跑未验证：本机不允许网络命名空间隔离（unshare: Operation not permitted）',
                         '须按 docs/离线部署演练清单.md 在目标环境执行并逐条记录'],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('\n可查项：' + ('全部通过' if not problems else '存在问题（见 problems）'))
    print('未验证项：' + '；'.join(report['not_verified']))
    return 0 if not problems else 1


if __name__ == '__main__':
    raise SystemExit(main())