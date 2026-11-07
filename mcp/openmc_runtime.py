"""Trusted runtime configuration and workspace boundaries; no Harness dependency."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

import business

ROOT = Path(__file__).resolve().parents[1]
Error = business.MCInputError


def workspace() -> Path:
    root = business.WORKSPACE.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_in_workspace(value: str, *, exists: bool = True, internal: bool = False) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise Error("路径须为MC_WORKSPACE内的相对路径")
    parts = Path(value).parts
    if any(p == '..' or (p.startswith('.mc-') and not internal) for p in parts):
        raise Error("不能访问上级目录或内部作业记录")
    root = workspace()
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise Error("工具不接受符号链接路径")
    if not path.resolve().is_relative_to(root):
        raise Error("路径超出MC_WORKSPACE")
    if exists and not path.exists():
        raise Error("文件或目录不存在：" + value)
    return path


def relative(path: Path) -> str:
    return path.relative_to(workspace()).as_posix()


def file_location(path: Path) -> dict:
    """Keep workspace-relative API paths and identify their server-side base."""
    root = workspace()
    return {'path': path.relative_to(root).as_posix(), 'workspace': str(root),
            'server_path': str(path), 'location': 'server'}


def config() -> dict:
    local = ROOT / 'config/openmc.local.json'
    source = Path(os.environ.get('MC_OPENMC_CONFIG', local if local.exists() else ROOT / 'config/openmc.json'))
    if not source.is_absolute():
        source = ROOT / source
    try:
        value = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Error("无法读取OpenMC配置；检查MC_OPENMC_CONFIG") from exc
    if value.get('runtime') != 'native':
        raise Error("首版支持native运行；容器部署请在容器内启动工具服务并配置native")
    for key in ('python_command', 'openmc_command'):
        command = value.get(key)
        if not isinstance(command, list) or not command or any(not isinstance(v, str) or not v or '\0' in v for v in command):
            raise Error(key + "须为管理员配置的非空参数数组")
    for key in ('api_timeout_seconds', 'job_timeout_seconds', 'max_log_bytes'):
        if type(value.get(key)) is not int or value[key] <= 0:
            raise Error(key + "须为正整数")
    if 'cad_python_command' in value:
        cad_command = value['cad_python_command']
        if not isinstance(cad_command, list) or not cad_command or any(not isinstance(v, str) or not v or '\0' in v for v in cad_command):
            raise Error('cad_python_command须为管理员配置的非空参数数组')
    if 'cad_timeout_seconds' in value and (type(value['cad_timeout_seconds']) is not int or value['cad_timeout_seconds'] <= 0):
        raise Error('cad_timeout_seconds须为正整数')
    if 'cad_library_path' in value and (not isinstance(value['cad_library_path'], str) or '\0' in value['cad_library_path']):
        raise Error('cad_library_path须为字符串')
    if 'geometry_check_timeout_seconds' in value and (type(value['geometry_check_timeout_seconds']) is not int or value['geometry_check_timeout_seconds'] <= 0):
        raise Error('geometry_check_timeout_seconds须为正整数')
    if 'max_concurrent_jobs' in value and (type(value['max_concurrent_jobs']) is not int or value['max_concurrent_jobs'] < 0):
        raise Error('max_concurrent_jobs须为非负整数')
    if 'max_input_bytes' in value and (type(value['max_input_bytes']) is not int or value['max_input_bytes'] <= 0):
        raise Error('max_input_bytes须为正整数')
    from openmc_storage import reserve_bytes, check_paths
    value['min_free_disk_bytes'] = reserve_bytes(value)
    value['disk_space_check_paths'] = check_paths(value)
    for key in ('max_scan_cases', 'max_plot_points', 'max_report_rows', 'report_timeout_seconds'):
        if key in value and (type(value[key]) is not int or value[key] <= 0):
            raise Error(key + '须为正整数')
    if not isinstance(value.get('expected_version'), str) or not value['expected_version']:
        raise Error("expected_version须填写已核实的OpenMC版本")
    if value.get('data_kind') not in ('user_supplied_unverified', 'mock_process_fixture'):
        raise Error("data_kind须区分未审定用户数据与测试用模拟进程")
    return value


def provenance(directory: Path, cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    marker = directory / 'mc-case.json'
    if marker.exists():
        if marker.is_symlink():
            raise Error("算例说明不能是符号链接")
        try:
            data = json.loads(marker.read_text(encoding='utf-8'))
            provenance = {'data_kind': data['data_kind'], 'template': data.get('template'),
                          'execution_kind': 'mock_process_fixture' if cfg['data_kind'] == 'mock_process_fixture' else 'openmc_runtime',
                          'professional_acceptance': False, 'note': data.get('note', ''),
                          **{key: data[key] for key in ('source_url', 'source_sha256') if key in data}}
            # Validation tags travel with the job, not only with the build result.
            if data.get('validation') is not None:
                provenance['validation'] = data['validation']
            return provenance
        except (OSError, ValueError, KeyError) as exc:
            raise Error("算例来源说明损坏：mc-case.json") from exc
    return {'data_kind': cfg['data_kind'],
            'execution_kind': 'mock_process_fixture' if cfg['data_kind'] == 'mock_process_fixture' else 'openmc_runtime',
            'professional_acceptance': False,
            'note': '输入数据尚未经本项目专业验收。'}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def command(cfg: dict, directory: Path, *, action: str | None = None,
            arguments: list[str] | None = None) -> list[str]:
    arguments = arguments or []
    if action:
        return cfg['python_command'] + [str(Path(__file__).resolve().parent / 'openmc_worker.py'), action] + arguments
    return cfg['openmc_command'] + arguments


def api(action: str, directory: Path, payload: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    payload = {**payload, '_expected_version': cfg['expected_version'], '_font_path': cfg.get('font_path'),
               '_openmc_exec': cfg['openmc_command'][0]}
    try:
        result = subprocess.run(command(cfg, directory, action=action), cwd=directory,
                                input=json.dumps(payload, allow_nan=False), text=True,
                                capture_output=True, timeout=cfg['api_timeout_seconds'],
                                env=__import__('nuclear_data').runtime_env(cfg))
    except FileNotFoundError as exc:
        raise Error("运行时程序不存在；检查python_command安装与配置") from exc
    except subprocess.TimeoutExpired as exc:
        raise Error("OpenMC API调用超时；检查运行环境和输入") from exc
    if result.returncode:
        # Worker errors contain actionable validation text; no environment is printed.
        detail = result.stderr.strip().splitlines()[-1:] or ['unknown error']
        raise Error('OpenMC API失败：' + detail[0][:1000])
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except ValueError as exc:
        raise Error("OpenMC API适配器未返回JSON对象") from exc
