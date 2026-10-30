"""Managed public HDF5 cache. Downloads are explicit workflow steps, never fake data."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from openmc_runtime import ROOT, Error, atomic_json

LIBRARIES = {
    'official-endfb-vii.1': {'name':'OpenMC official ENDF/B-VII.1',
        'repository':'https://openmc.org/data/',
        'revision':'5bc4b62a602d569e3a801c3499f03378192e83623c3eb39500804178706781f9',
        'archive':True},
    'endfb-vii.1': {'name': 'ENDF/B-VII.1 NNDC',
        'repository': 'openmc-data-storage/ENDF-B-VII.1-NNDC',
        'revision': '70ae5246d1ce56330035979b7b6c32b4a252156f'},
    'endfb-viii.0': {'name': 'ENDF/B-VIII.0 NNDC',
        'repository': 'openmc-data-storage/ENDF-B-VIII.0-NNDC',
        'revision': '466ab3042f70e60b693fbbd3f6f15f30dba7cd1d'},
}


def library(cfg: dict | None = None) -> tuple[str, dict]:
    # Old configurations retain their existing cache; new official instances
    # explicitly select the complete VII.1 neutron/thermal evaluation.
    key = (cfg or {}).get('nuclear_data_library', 'endfb-viii.0')
    if key not in LIBRARIES:raise Error('未知核数据库：' + str(key))
    return key, LIBRARIES[key]



def data_digest(path: Path, algorithm: str = 'sha256', git_blob: bool = False) -> str:
    value = hashlib.new(algorithm)
    if git_blob:value.update(b'blob ' + str(path.stat().st_size).encode() + b'\0')
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):value.update(block)
    return value.hexdigest()


def fetch_file(url: str, destination: Path, expected_size: int,
               *, expected_sha256: str | None = None, expected_git_sha: str | None = None,
               max_time_seconds: int = 90) -> None:
    """Bounded, resumable downloads are verified against fixed upstream object IDs."""
    transfer = destination.with_name(destination.name + '.download')
    for attempt in range(6):
        if transfer.exists() and transfer.stat().st_size > expected_size:
            transfer.replace(transfer.with_name(transfer.name + '.oversize'))
        if transfer.exists() and transfer.stat().st_size == expected_size:
            valid = (expected_sha256 is None or data_digest(transfer) == expected_sha256) and (
                expected_git_sha is None or data_digest(transfer,'sha1',True) == expected_git_sha)
            if valid:transfer.replace(destination);return
            transfer.replace(transfer.with_name(transfer.name + '.hash-mismatch'))
        offset = transfer.stat().st_size if transfer.exists() else 0
        address = url + ('&' if '?' in url else '?') + 'mc_attempt=' + str(attempt)
        print(f'下载 {destination.name}：{offset}/{expected_size} 字节，第{attempt+1}次', flush=True)
        command = ['curl','--http1.1','--fail','--location','--silent','--show-error',
                   '--connect-timeout','15','--max-time',str(max_time_seconds),'--speed-limit','1024','--speed-time','30']
        if offset:command += ['--continue-at', str(offset)]
        result = subprocess.run(command + [address,'-o',str(transfer)])
        if result.returncode == 33 and transfer.exists():
            transfer.replace(transfer.with_name(transfer.name + '.range-unsupported'))
    if transfer.exists() and transfer.stat().st_size == expected_size:
        valid = (expected_sha256 is None or data_digest(transfer) == expected_sha256) and (
            expected_git_sha is None or data_digest(transfer,'sha1',True) == expected_git_sha)
        if valid:transfer.replace(destination);return
    raise Error('公开核数据下载未完成或身份不匹配，保留断点文件：' + destination.name)


def managed_directory(cfg: dict | None = None) -> Path:
    return ROOT / '.runtime/nuclear-data' / library(cfg)[0]


def initialize_index(cfg: dict | None = None) -> Path:
    directory = managed_directory(cfg)
    if library(cfg)[1].get('archive'):
        index=directory/'cross_sections.xml'
        expected=json.loads((ROOT/'config/data-index/official-endfb-vii.1-files.json').read_text())
        if not index.is_file() or index.is_symlink() or data_digest(index)!=expected['files']['cross_sections.xml']:
            raise Error('官方多温度核数据库未初始化或索引已改变；先运行python3 scripts/install_official_data.py')
        return index
    directory.mkdir(parents=True, exist_ok=True)
    index = directory / 'cross_sections.xml'
    # Atomic initialization and explicit catalogue identity preserve concurrent callers.
    with (directory / '.index.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not index.exists():
            tree = ET.parse(ROOT / ('config/data-index/' + library(cfg)[0] + '.xml'))
            tree.write(directory / 'cross_sections.xml.pending', encoding='utf-8', xml_declaration=True)
            (directory / 'cross_sections.xml.pending').replace(index)
    return index


def runtime_env(cfg: dict) -> dict:
    env = dict(os.environ)
    configured = cfg.get('cross_sections') or env.get('OPENMC_CROSS_SECTIONS')
    if configured:
        env['OPENMC_CROSS_SECTIONS'] = str(Path(configured).resolve())
    elif (managed_directory(cfg) / 'cross_sections.xml').is_file():
        env['OPENMC_CROSS_SECTIONS'] = str((managed_directory(cfg) / 'cross_sections.xml').resolve())
    bins = [str(Path(cfg[key][0]).parent) for key in ('openmc_command', 'python_command')
            if Path(cfg[key][0]).is_absolute()]
    env['PATH'] = os.pathsep.join(bins + [env.get('PATH', '')])
    env.setdefault('MPLBACKEND', 'Agg')
    return env


def ensure_for_model(directory: Path, cfg: dict, extra_nuclides: list[str] | None = None) -> dict:
    settings = ET.parse(directory / ('model.xml' if (directory / 'model.xml').exists() else 'settings.xml')).getroot()
    mode = settings.findtext('.//energy_mode', 'continuous-energy')
    materials = ET.parse(directory / ('model.xml' if (directory / 'model.xml').exists() else 'materials.xml')).getroot()
    source = materials.findtext('.//cross_sections')
    if mode == 'multi-group':
        source = source or cfg.get('mg_cross_sections') or os.environ.get('OPENMC_MG_CROSS_SECTIONS')
        if not source:
            raise Error('多群例题缺少materials.cross_sections输入')
        path = Path(source)
        path = path if path.is_absolute() else directory / path
        if not path.is_file():raise Error('多群截面文件不存在：' + source)
        import openmc
        try:mgxs=openmc.MGXSLibrary.from_hdf5(path)
        except (OSError,ValueError,KeyError) as exc:raise Error('多群截面输入不是可读的OpenMC MGXS库：'+str(exc)) from exc
        names={x.name for x in mgxs.xsdatas}
        missing={n.attrib['name'] for n in materials.findall('.//macroscopic')}-names
        if missing:raise Error('多群库缺模型所需宏观截面组：'+', '.join(sorted(missing)))
        return {'available': True, 'data_kind': 'model_supplied_multigroup', 'files': [str(path.resolve())]}
    key, provider = library(cfg)
    index = managed_directory(cfg)/'cross_sections.xml'
    env = runtime_env(cfg)
    configured = Path(source) if source else Path(env.get('OPENMC_CROSS_SECTIONS', str(index)))
    if not configured.is_absolute():configured=directory/configured
    configured=configured.resolve()
    if configured==index.resolve():initialize_index(cfg)
    required = {('neutron', n.attrib['name']) for n in materials.findall('.//nuclide')}
    required.update(('thermal', n.attrib['name']) for n in materials.findall('.//sab'))
    if settings.findtext('.//photon_transport', 'false') in ('true','1'):
        import re
        required.update(('photon', re.match('[A-Z][a-z]?', n.attrib['name'])[0])
                        for n in materials.findall('.//nuclide'))
    index_root=ET.parse(configured).getroot()
    declared_directory=index_root.find('directory')
    data_base=Path(declared_directory.text or '.') if declared_directory is not None else configured.parent
    # OpenMC joins an explicit <directory> with each library path; relative
    # directories are relative to the solver cwd, which is this model directory.
    if not data_base.is_absolute():data_base=directory/data_base
    entries = index_root.findall('library')
    records = {}
    for e in entries:
        for name in e.attrib.get('materials', '').split():
            records[(e.attrib['type'], name)] = e.attrib['path']
    # Decay-only chain members legitimately have no neutron data. OpenMC
    # evolves them by decay; prefetch only chain nuclides actually in the XS index.
    required.update(('neutron', name) for name in (extra_nuclides or []) if ('neutron', name) in records)
    missing_names = sorted(name for kind, name in required if (kind, name) not in records)
    if missing_names:raise Error('核数据库索引未提供所需核素/热散射：' + ', '.join(missing_names))
    if configured!=index.resolve():
        external=[]
        for kind,name in sorted(required):
            path=Path(records[(kind,name)])
            path=path if path.is_absolute() else data_base/path
            if not path.is_file():raise Error('外部核数据库缺文件：'+str(path))
            external.append({'kind':kind,'name':name,'server_path':str(path.resolve()),'sha256':data_digest(path)})
        return {'available':True,'data_kind':'external_user_supplied_unverified',
                'library':'external_unverified','source_revision':None,
                'cross_sections':str(configured),'index_sha256':data_digest(configured),
                'files':[x['server_path'] for x in external],'file_identities':external,
                'note':'按模型或管理员配置访问的外部数据；身份未与固定官方清单匹配，不标作官方版本或专业验收。'}
    identities = json.loads((ROOT / ('config/data-index/' + key + '-files.json')).read_text())['files']
    if provider.get('archive') and configured.resolve()==index.resolve():
        if settings.findtext('.//temperature/multipole','false') in ('true','1'):
            required.update(('wmp',name) for kind,name in list(required) if kind=='neutron' and ('wmp',name) in records)
        files=[]
        for pair in sorted(required):
            name=records[pair];path=configured.parent/name
            if Path(name).is_absolute() or '..' in Path(name).parts or name not in identities:
                raise Error('官方数据索引路径不在固定清单中：'+name)
            if path.is_symlink() or not path.is_file() or data_digest(path)!=identities[name]:
                raise Error('官方核数据缺失或与固定版本不符：'+name)
            files.append(str(path.resolve()))
        return {'available':True,'library':provider['name'],'source_revision':provider['revision'],
                'cross_sections':str(configured),'files':files,
                'note':'真实官方多温度截面；保留原模型温度与求解设置，非专业验收结论。'}
    if configured.resolve()==index.resolve():
        unavailable=[records[pair] for pair in sorted(required) if records[pair] not in identities]
        if unavailable:raise Error('固定公开库缺少真实文件，未开始下载；提供完整管理员数据或选择另一完整库：'+', '.join(unavailable))
    def prepare_one(pair):
        kind, name = pair
        relative = records[(kind, name)]; p = Path(relative)
        target = p if p.is_absolute() else configured.parent / p
        if configured.resolve() != index.resolve():
            if not target.is_file():raise Error('管理员核数据库缺文件：' + str(target))
            return str(target.resolve())
        if p.is_absolute() or '..' in p.parts or not relative.endswith('.h5'):
            raise Error('受管核数据索引路径无效')
        descriptor = identities.get(relative)
        if descriptor is None:raise Error('公开库文件未锁定版本身份：' + relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with (target.parent / (target.name + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            record_path = target.with_suffix('.source.json')
            if target.exists():
                if not record_path.exists():raise Error('已有核数据缺来源记录，未覆盖：' + str(target))
                old = json.loads(record_path.read_text())
                pointer = old.get('upstream_lfs_pointer')
                pointer_identity = (hashlib.sha1(b'blob ' + str(len(pointer.encode())).encode()+b'\0'+pointer.encode()).hexdigest()==descriptor['git_blob_sha']) if pointer else False
                upstream_valid = ((pointer_identity and data_digest(target) == pointer.split('oid sha256:')[1].splitlines()[0])
                                  if pointer else data_digest(target,'sha1',True) == descriptor['git_blob_sha'])
                if old.get('revision') != provider['revision'] or not upstream_valid or data_digest(target) != old['sha256']:
                    raise Error('核数据文件与已登记哈希不一致：' + name)
            else:
                print(f'准备公开核数据：{name} ({kind})', flush=True)
                base = f"https://raw.githubusercontent.com/{provider['repository']}/{provider['revision']}/h5_files/"
                url = base + relative
                expected_size = descriptor['bytes']
                expected_sha256 = None
                pointer_text = None
                if expected_size < 256:
                    pointer = target.with_suffix('.lfs-pointer')
                    fetch_file(url + '?download=1', pointer, expected_size,
                               expected_git_sha=descriptor['git_blob_sha'])
                    pointer_text = pointer.read_text()
                    import re
                    match = re.fullmatch(r'version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?', pointer_text)
                    if not match:raise Error('固定版本的LFS指针格式无效：' + name)
                    expected_sha256, expected_size = match[1], int(match[2])
                    url = base.replace('https://raw.githubusercontent.com/', 'https://media.githubusercontent.com/media/') + relative
                part = target.with_suffix('.h5.part')
                fetch_file(url + '?download=1', part, expected_size,
                           expected_sha256=expected_sha256,
                           expected_git_sha=None if pointer_text else descriptor['git_blob_sha'])
                with part.open('rb') as f:
                    if f.read(8) != b'\x89HDF\r\n\x1a\n':raise Error('下载内容不是HDF5：' + name)
                digest = data_digest(part)
                part.replace(target)
                atomic_json(record_path, {'library': provider['name'], 'repository': provider['repository'],
                    'revision': provider['revision'], 'source_url': url, 'sha256': digest, 'nuclide_or_sab': name, 'kind': kind,
                    'upstream_git_blob_sha': descriptor['git_blob_sha'], 'upstream_lfs_pointer': pointer_text})
        return str(target.resolve())
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as downloads:
        files = list(downloads.map(prepare_one, sorted(required)))
    return {'available': True, 'library': provider['name'], 'source_revision': provider['revision'],
            'cross_sections': str(configured), 'files': files,
            'note': '按固定公开库准备模型所需文件；不代表甲方专业结果已验收。'}
