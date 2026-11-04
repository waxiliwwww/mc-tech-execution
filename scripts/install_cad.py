#!/usr/bin/env python3
"""Install and verify the project's independent CAD runtime; no system installation."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / 'config/cad-requirements.txt'
SPEC = ROOT / 'config/cad-runtime.json'


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix='.cad-pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write('\n')
        os.chmod(filename, 0o600)
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


def pinned_versions():
    return {name.lower().replace('_', '-'): version
            for line in REQUIREMENTS.read_text().splitlines()
            if line.strip() and not line.startswith('#')
            for name, version in [line.strip().split('==', 1)]}


def installed_versions(python):
    code = "import importlib.metadata,json;print(json.dumps({d.metadata['Name'].lower().replace('_','-'):d.version for d in importlib.metadata.distributions()}))"
    return json.loads(subprocess.check_output([str(python), '-c', code], text=True, timeout=30))


def package_valid(path, size, algorithm, expected):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        return False
    actual = hashlib.new(algorithm)
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            actual.update(block)
    return actual.hexdigest() == expected.lower()


def _native_packages(prefix, names):
    """APT selects packages for this machine; download and unpack only, never install."""
    for command in ('apt-get', 'apt-cache', 'dpkg-deb', 'curl'):
        if not shutil.which(command):
            raise ValueError('CAD原生库自动准备需要Debian/Ubuntu的apt-get、dpkg-deb和curl；不执行系统安装。')
    query = subprocess.check_output([
        'apt-get', '--print-uris', '--download-only', '--reinstall', '--yes',
        '--no-install-recommends', '-o', 'Debug::NoLocking=true', 'install', *names], text=True)
    cache = prefix / 'packages'
    cache.mkdir(parents=True, exist_ok=True)
    records = []
    for line in query.splitlines():
        if not line.startswith("'http"):
            continue
        uri, filename, size, expected_hash = shlex.split(line)
        # Only regular repository filenames from APT are used; no paths or archives
        # selected by an MCP caller enter the deployment installer.
        if Path(filename).name != filename:
            raise ValueError('APT返回了非普通软件包文件名，未下载')
        package, version, _architecture = filename.rsplit('_', 2)
        metadata = subprocess.check_output([
            'apt-cache', 'show', '--no-all-versions', package + '=' + unquote(version)], text=True)
        checksums = [row.split(':', 1)[1].strip() for row in metadata.splitlines()
                     if row.startswith('SHA256:')]
        if len(checksums) != 1:
            raise ValueError('APT索引未提供唯一SHA256，未下载：' + filename)
        algorithm, expected = 'sha256', checksums[0]
        size = int(size)
        target = cache / filename
        if not package_valid(target, size, algorithm, expected):
            if target.exists():
                target.rename(cache / (filename + '.invalid-' + uuid.uuid4().hex[:8]))
            temporary = cache / (filename + '.partial-' + uuid.uuid4().hex[:8])
            secure_uri = 'https://' + uri[7:] if uri.startswith('http://') else uri
            subprocess.run(['curl', '--fail', '--location', '--retry', '1', '--max-time', '180',
                            secure_uri, '--output', str(temporary)], check=True, timeout=370)
            if not package_valid(temporary, size, algorithm, expected):
                raise ValueError('CAD原生包大小/校验和不符：' + filename)
            temporary.rename(target)
        subprocess.run(['dpkg-deb', '--extract', str(target), str(prefix)], check=True, timeout=60)
        records.append({'file': filename, 'bytes': size, 'sha256': digest(target),
                        'apt_digest': expected_hash, 'url': uri})
    return records


def native_packages(prefix, names):
    prefix.mkdir(parents=True, exist_ok=True)
    with (prefix / '.install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _native_packages(prefix, names)


def cad_environment(library):
    env = dict(os.environ)
    if library:
        env['LD_LIBRARY_PATH'] = str(library) + os.pathsep + env.get('LD_LIBRARY_PATH', '')
    return env


def run_probe(python, library, directory, spec):
    """A real STEP import and H5M conversion, not a dependency-only smoke test."""
    source = ROOT / spec['probe_step']
    if digest(source) != spec['probe_sha256']:
        raise ValueError('随包CAD探针STEP指纹不符，未继续安装检查')
    directory.mkdir(parents=True, exist_ok=True)
    source_copy = directory / 'cube.step'
    if source_copy.exists() and digest(source_copy) != digest(source):
        raise ValueError('CAD安装探针输入已改变，保留现场，未覆盖')
    if not source_copy.exists():
        shutil.copyfile(source, source_copy)
    inspect_result = directory / 'inspect.json'
    conversion_result = directory / 'conversion.json'
    output = directory / 'cube.h5m'
    worker = ROOT / 'mcp/cad_worker.py'
    for action, payload, result in [
        ('inspect-step', {'step': str(source_copy)}, inspect_result),
        ('convert', {'step': str(source_copy), 'material_tags': ['cad_probe'], 'scale_factor': 0.1,
                     'output': str(output)}, conversion_result),
    ]:
        payload['_result_path'] = str(result)
        run = subprocess.run([str(python), str(worker), action], input=json.dumps(payload),
                             text=True, capture_output=True, env=cad_environment(library),
                             cwd=directory, timeout=120)
        (directory / (action + '.stdout.log')).write_text(run.stdout)
        (directory / (action + '.stderr.log')).write_text(run.stderr)
        value = json.loads(result.read_text()) if result.is_file() else {}
        if run.returncode != 0 or not value.get('ok'):
            raise ValueError('CAD实际安装探针失败：' + str(value.get('error', '详见' + str(directory))))
    inspected = json.loads(inspect_result.read_text())['result']
    converted = json.loads(conversion_result.read_text())['result']
    bounds = inspected.get('bounding_box') or {}
    if inspected['solids'] != 1 or any(abs(bounds.get(axis, float('inf')) - expected) > 1e-6
                                     for axis, expected in [('xmin', -5), ('xmax', 5),
                                                            ('ymin', -5), ('ymax', 5),
                                                            ('zmin', -5), ('zmax', 5)]):
        raise ValueError('CAD探针STEP实体/毫米制包围盒与预期不符')
    if (converted.get('solids') != 1 or converted.get('source_sha256') != digest(source_copy)
            or converted.get('sha256') != digest(output)
            or output.read_bytes()[:8] != b'\x89HDF\r\n\x1a\n'):
        raise ValueError('CAD转换产物与同一STEP的来源/指纹不符')
    # Read actual mesh coordinates without importing OpenMC into the CAD runtime.
    code = "import h5py,json,sys,numpy as np;f=h5py.File(sys.argv[1]);a=np.asarray(f['tstt/nodes/coordinates']);print(json.dumps({'min':a.min(axis=0).tolist(),'max':a.max(axis=0).tolist()}))"
    mesh_bounds = json.loads(subprocess.check_output([str(python), '-c', code, str(output)],
                                                   env=cad_environment(library), text=True, timeout=30))
    if any(abs(v - e) > 1e-6 for key, e in [('min', -0.5), ('max', 0.5)]
           for v in mesh_bounds[key]):
        raise ValueError('CAD探针H5M未正确完成mm→cm换算')
    return {'source_sha256': digest(source_copy), 'h5m_sha256': digest(output),
            'h5m_bytes': output.stat().st_size, 'mesh_bounds_cm': mesh_bounds,
            'conversion_versions': converted['versions'], 'scale_factor': 0.1,
            'solids': 1, 'directory': str(directory), 'transport_executed': False}


def update_config(path, python, library):
    """Only publish CAD fields after a real probe succeeds; keep all other keys."""
    path = Path(path)
    value = json.loads(path.read_text()) if path.exists() else json.loads((ROOT / 'config/openmc.json').read_text())
    # Keep the venv interpreter path: resolving bin/python's symlink would point
    # at the base interpreter and lose the newly installed CAD site-packages.
    value.update(cad_python_command=[str(python.absolute())], cad_library_path=str(library.resolve()))
    atomic_json(path, value)


def check_install_space(directory, config_path):
    """Reserve room for the roughly 2 GiB runtime plus transient wheel extraction."""
    source = config_path if config_path.is_file() else ROOT / 'config/openmc.json'
    cfg = json.loads(source.read_text())
    reserve = cfg.get('min_free_disk_bytes', 1024 ** 3)
    checks = cfg.get('disk_space_check_paths', [])
    if type(reserve) is not int or reserve < 0 or not isinstance(checks, list):
        raise ValueError('磁盘保留空间配置不合法，未安装CAD依赖')
    for path in [directory, *map(Path, checks)]:
        required = 3 * 1024 ** 3 + reserve
        if shutil.disk_usage(path).free < required:
            raise ValueError('CAD初配空间不足：' + str(path) + '；需留出运行时/临时解包空间及配置的保留空间，未继续安装')


def install(prefix, python, native_prefix, config_path):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise ValueError('固定CAD运行环境当前验证于Linux/WSL x86_64；其他平台请手动配置CAD环境。')
    spec = json.loads(SPEC.read_text())
    version = json.loads(subprocess.check_output([str(python), '-c', 'import sys,json;print(json.dumps(list(sys.version_info[:2])))'], text=True))
    if version < spec['python_minimum']:
        raise ValueError('固定CAD环境需要Python 3.12+；quickstart会使用已准备的OpenMC Python。')
    prefix = prefix.resolve()
    native_prefix = native_prefix.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with (prefix.parent / ('.' + prefix.name + '.install.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        interpreter = prefix / 'bin/python'
        pending = prefix / '.mc-cad-install-pending.json'
        if not interpreter.is_file():
            if prefix.exists() and any(prefix.iterdir()):
                raise ValueError('CAD目标目录非空且不是Python环境，未覆盖：' + str(prefix))
            check_install_space(prefix.parent, config_path)
            subprocess.run([str(python), '-m', 'venv', str(prefix)], check=True, timeout=60)
            atomic_json(pending, {'requirements_sha256': digest(REQUIREMENTS)})
        actual_python = json.loads(subprocess.check_output([
            str(interpreter), '-c', 'import sys,json;print(json.dumps(list(sys.version_info[:2])))'], text=True))
        if actual_python < spec['python_minimum']:
            raise ValueError('现有CAD解释器低于Python 3.12；环境未修改，请指定新--prefix。')
        pinned = pinned_versions()
        installed = installed_versions(interpreter)
        missing = {name: version for name, version in pinned.items() if installed.get(name) != version}
        if missing:
            state = json.loads(pending.read_text()) if pending.is_file() else {}
            if state.get('requirements_sha256') != digest(REQUIREMENTS):
                raise ValueError('现有CAD环境与固定依赖不符，未原地改装；请指定新的--prefix目录：' + str(prefix))
            check_install_space(prefix.parent, config_path)
            args = [str(interpreter), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--no-cache-dir', '--only-binary=:all:', '--no-deps', '-r', str(REQUIREMENTS)]
            if os.environ.get('WHEELHOUSE'):
                args += ['--no-index', '--find-links', os.environ['WHEELHOUSE']]
            subprocess.run(args, check=True, timeout=1200)
        subprocess.run([str(interpreter), '-m', 'pip', 'check'], check=True, timeout=60)
        actual = installed_versions(interpreter)
        if any(actual.get(name) != version for name, version in pinned.items()):
            raise ValueError('CAD安装后版本与固定清单不一致，未更新配置')
        library = native_prefix / 'usr/lib/x86_64-linux-gnu'
        packages = native_packages(native_prefix, spec['native_packages'])
        probe_root = prefix / 'mc-cad-probes'
        probe = run_probe(interpreter, library, probe_root / uuid.uuid4().hex, spec)
        receipt = {'installer_version': 1, 'requirements_sha256': digest(REQUIREMENTS),
                   'python_version': actual_python, 'versions': {name: actual[name] for name in pinned},
                   'native_packages': packages, 'native_library_path': str(library), 'probe': probe}
        atomic_json(prefix / 'mc-cad-install.json', receipt)
        update_config(config_path, interpreter, library)
        pending.unlink(missing_ok=True)
        print('CAD环境已准备：同一STEP读取、mm→cm转换和H5M坐标/指纹检查通过。', flush=True)
        print('运行环境：' + str(interpreter) + '\n配置：' + str(config_path), flush=True)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=ROOT / '.runtime/cadenv')
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--native-prefix', type=Path, default=ROOT / '.runtime/cad-native')
    parser.add_argument('--config', type=Path, default=ROOT / 'config/openmc.local.json')
    args = parser.parse_args()
    try:
        install(args.prefix, args.python, args.native_prefix, args.config)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('CAD准备失败，现有配置保持不变：' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
