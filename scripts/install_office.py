#!/usr/bin/env python3
"""Prepare a user-local LibreOffice on Debian/Ubuntu; never run package install scripts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import uuid
from urllib.parse import unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--prefix', default=str(ROOT / '.runtime/office'))
args = parser.parse_args()
prefix = Path(args.prefix).resolve()
prefix.mkdir(parents=True, exist_ok=True)
with (prefix / '.install.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    launcher = prefix / 'soffice'
    if launcher.is_file():
        with tempfile.TemporaryDirectory(dir=prefix, prefix='.check-profile-') as profile:
            try:
                check = subprocess.run([str(launcher), '-env:UserInstallation=' + Path(profile).as_uri(), '--headless', '--version'], capture_output=True, timeout=30)
                if check.returncode == 0:
                    print('已准备Office：' + check.stdout.decode().strip() + '；' + str(launcher))
                    raise SystemExit(0)
            except (OSError, subprocess.TimeoutExpired):pass
    if not shutil.which('apt-get') or not shutil.which('dpkg-deb') or not shutil.which('curl'):
        raise SystemExit('自动准备Office需要Debian/Ubuntu的apt-get、dpkg-deb和curl；其他系统先安装LibreOffice，再配置office_command。')
    query = subprocess.run(['apt-get', '--print-uris', '--download-only', '--yes', '--no-install-recommends',
                            '-o', 'Debug::NoLocking=true', 'install', 'libreoffice-writer', 'fonts-noto-cjk'],
                           capture_output=True, text=True, check=True)
    packages = []
    for line in query.stdout.splitlines():
        if not line.startswith("'http"):continue
        uri, filename, size, _digest = shlex.split(line)
        package, version, _arch = filename.rsplit('_', 2)
        packages.append((package + '=' + unquote(version), filename, int(size), uri, _digest))
    if not packages and shutil.which('soffice'):
        print('系统已提供LibreOffice：' + shutil.which('soffice'))
        raise SystemExit(0)
    cache = prefix / 'packages'; cache.mkdir(exist_ok=True)
    print(f'准备Office与字体：{len(packages)}个包，下载约{sum(p[2] for p in packages)//1048576}MiB；只写入{prefix}', flush=True)
    def download(package):
        specification, filename, expected_size, uri, expected_hash = package
        algorithm, digest=expected_hash.split(':',1)
        algorithm=algorithm.lower().removesuffix('sum')
        def valid(path):
            if not path.is_file() or path.is_symlink() or path.stat().st_size!=expected_size:return False
            actual=hashlib.new(algorithm)
            with path.open('rb') as source:
                for block in iter(lambda:source.read(1024*1024),b''):actual.update(block)
            return actual.hexdigest()==digest.lower()
        target=cache/filename
        if valid(target):return
        if target.exists():target.rename(cache/(filename+'.invalid-'+uuid.uuid4().hex[:8]))
        temporary=cache/(filename+'.partial-'+uuid.uuid4().hex[:8])
        # HTTPS also works on hosts whose proxy blocks the distribution's
        # default HTTP URLs. APT still supplies the package version/hash.
        secure_uri='https://'+uri[7:] if uri.startswith('http://') else uri
        subprocess.run(['curl','--fail','--location','--retry','1','--max-time','180',secure_uri,'--output',str(temporary)],
                       check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=370)
        if not valid(temporary):raise ValueError('Office软件包大小/校验和不符：'+specification)
        temporary.rename(target)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(download, packages))
    for archive in [cache / filename for _specification, filename, _size, _uri, _digest in packages]:
        subprocess.run(['dpkg-deb', '--extract', str(archive), str(prefix)], check=True)
    for path in prefix.rglob('*'):
        if path.is_symlink():
            original = Path(os.readlink(path))
            if original.is_absolute() and (prefix / original.relative_to('/')).exists():
                path.unlink(); path.symlink_to(os.path.relpath(prefix / original.relative_to('/'), path.parent))
    program = prefix / 'usr/lib/libreoffice/program/soffice.bin'
    if not program.exists():raise SystemExit('Office未准备完整；请保留输出并检查APT源')
    fundamental = program.parent / 'fundamentalrc'
    fundamental.write_text(fundamental.read_text().replace('BRAND_BASE_DIR=file:///usr/lib/libreoffice',
                                                          'BRAND_BASE_DIR=${ORIGIN}/..'))
    # Debian creates these configuration files via ucf during system installation.
    # Supply the packaged defaults locally instead of executing privileged postinst.
    for source in (prefix / 'usr/lib/libreoffice/share/.registry').glob('*'):
        destination = prefix / 'usr/lib/libreoffice/share/registry' / source.name
        if source.is_file() and (not destination.exists()):
            if destination.is_symlink():destination.unlink()
            shutil.copy2(source, destination)
    fonts = ET.Element('fontconfig')
    ET.SubElement(fonts, 'dir').text = str(prefix / 'usr/share/fonts')
    ET.SubElement(fonts, 'dir').text = '/usr/share/fonts'
    ET.SubElement(fonts, 'cachedir').text = str(prefix / 'font-cache')
    ET.SubElement(fonts, 'include', {'ignore_missing': 'yes'}).text = '/etc/fonts/conf.d'
    ET.ElementTree(fonts).write(prefix / 'fonts.conf', encoding='utf-8', xml_declaration=True)
    launcher = prefix / 'soffice'
    launcher.write_text('''#!/usr/bin/env bash
set -euo pipefail
office_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="$office_root/usr/lib/libreoffice/program:$office_root/usr/lib/x86_64-linux-gnu:$office_root/usr/lib/ure/lib:${LD_LIBRARY_PATH:-}"
export URE_BOOTSTRAP="vnd.sun.star.pathname:$office_root/usr/lib/libreoffice/program/fundamentalrc"
export FONTCONFIG_FILE="$office_root/fonts.conf"
exec "$office_root/usr/lib/libreoffice/program/oosplash" "$@"
''')
    launcher.chmod(0o755)
    with tempfile.TemporaryDirectory(dir=prefix, prefix='.check-profile-') as profile:
        subprocess.run([str(launcher), '-env:UserInstallation=' + Path(profile).as_uri(), '--headless', '--version'], check=True, timeout=30)
    print('Office与中文字体准备完成：' + str(launcher))
