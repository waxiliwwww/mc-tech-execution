#!/usr/bin/env python3
"""表4-2 准确率比对 harness（D6 第一刀）：读参考值文件→相同设置运行→按容差判定→出报告。

设计原则（与仓库其他入口一致）：
- 通过 MCP CLI 驱动，不绕过项目入口；示例/算例生成、校验、提交、读取全部走已注册工具。
- 参考值一律来自清单文件（manifest）：解析型参考从算例自身截面数据派生（不硬编码数字），
  常数型参考必须在清单中给出数值与来源；缺值如实判 INCONCLUSIVE，不猜、不冒充通过。
- 比对前校验：结构+引擎解析（api_check）；核数据版本登记：模板、引擎版本、截面文件
  sha256 与运行设置一并写入报告。
- 判定语义：PASS（全部已定义判据满足）/ FAIL（有判据不满足）/ INCONCLUSIVE（缺参考值、
  缺keff、设置不一致、校验未过、超时等，如实说明原因）。退出码：0=全PASS，1=有FAIL，2=有INCONCLUSIVE。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'tests/fixtures/accuracy/manifest.json'
DEFAULT_PYTHON = ROOT / 'mcp/.venv/bin/python'
TERMINAL = ('completed', 'failed', 'cancelled', 'partial', 'interrupted')


class HarnessError(ValueError):
    pass


def cli(python: str, name: str, arguments: dict, env: dict, timeout: int = 600) -> dict:
    completed = subprocess.run(
        [python, str(ROOT / 'mcp/cli.py'), 'call', name, json.dumps(arguments)],
        capture_output=True, text=True, timeout=timeout, env=env, cwd=str(ROOT))
    if completed.returncode:
        raise HarnessError(f'{name}调用失败：' + (completed.stderr or '').strip()[-800:])
    return json.loads(completed.stdout)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def settings_of(directory: Path) -> dict:
    root = ET.parse(directory / 'settings.xml').getroot()
    def number(tag):
        node = root.find(tag)
        return int(node.text) if node is not None and node.text else None
    return {'particles': number('particles'), 'batches': number('batches'),
            'inactive': number('inactive')}


def analytic_infinite_medium(python: str, case_dir: Path, xs_file: str) -> dict:
    """k∞=ΣνΣf/ΣΣa：齐次、反射边界、无泄漏单群系统的解析值；从算例自身MGXS文件派生。"""
    path = case_dir / xs_file
    if not path.is_file():
        raise HarnessError('解析参考所需的截面文件不存在：' + str(path))
    reader = ROOT / 'scripts/accuracy_xs_reader.py'
    completed = subprocess.run([python, str(reader), str(path)], capture_output=True, text=True,
                               timeout=120)
    if completed.returncode:
        raise HarnessError('截面文件读取失败：' + (completed.stderr or '').strip()[-600:])
    data = json.loads(completed.stdout)
    try:
        derived = str(path.relative_to(ROOT))
    except ValueError:
        derived = str(path)
    return {'value': data['k_inf'], 'energy_groups': data['groups'],
            'xs': {'absorption': data['absorption'], 'nu_fission': data['nu_fission']},
            'formula': 'k∞=ΣνΣf/ΣΣa（齐次反射边界无泄漏）',
            'derived_from': derived,
            'sha256': file_sha256(path)}


def runtime_python(env: dict) -> str:
    # 截面文件读取需要运行时的h5py，接口层venv不保证有；优先读openmc配置里的解释器。
    candidate = env.get('MC_OPENMC_CONFIG') or str(ROOT / 'config/openmc.local.json')
    try:
        command = json.loads(Path(candidate).read_text()).get('python_command') or []
        if command:
            return command[0]
    except (OSError, ValueError):
        pass
    return DEFAULT_PYTHON


def resolve_reference(python: str, spec: dict, case_dir: Path, xs_python: str | None = None) -> dict:
    kind = spec.get('type')
    if kind == 'constant':
        value = spec.get('value')
        if value is None:
            raise HarnessError('参考值未录入：' + str(spec.get('source') or '清单未给source'))
        if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
            raise HarnessError('参考值必须为有限数')
        result = {'value': float(value), 'source': spec.get('source'),
                  'type': 'constant'}
        sigma = spec.get('sigma')
        if sigma is not None:
            if type(sigma) not in (int, float) or isinstance(sigma, bool) or not sigma > 0:
                raise HarnessError('参考值sigma须为正数')
            result['sigma'] = float(sigma)
        return result
    if kind == 'analytic_infinite_medium':
        reader = xs_python or python
        return {'type': kind, **analytic_infinite_medium(reader, case_dir, spec['xs_file'])}
    raise HarnessError('未知参考值类型：' + str(kind))


def judge(want: dict, run: dict, reference: dict, tolerance: dict) -> dict:
    """按清单容差判定：sigma倍数（两倍误差同款口径）与相对偏差可分别启用。"""
    mean, std = run.get('mean'), run.get('std_dev')
    if mean is None or std is None:
        return {'verdict': 'INCONCLUSIVE', 'reason': '本次运行没有可用keff（未完成/固定源/NaN）'}
    if not math.isfinite(mean) or not math.isfinite(std) or std < 0:
        return {'verdict': 'INCONCLUSIVE', 'reason': 'keff数值异常（非有限或负σ）'}
    deviation = mean - reference['value']
    sigma_combined = math.hypot(std, reference.get('sigma') or 0.0)
    criteria = []
    passed = True
    if tolerance.get('sigma') is not None:
        limit = tolerance['sigma']
        inside = abs(deviation) <= limit * sigma_combined if sigma_combined > 0 else deviation == 0
        criteria.append({'criterion': f'|Δk| ≤ {limit}σ_合成', 'inside': bool(inside),
                         'value': abs(deviation), 'limit': limit * sigma_combined})
        passed = passed and inside
    if tolerance.get('relative') is not None:
        limit = tolerance['relative']
        ratio = abs(deviation) / abs(reference['value']) if reference['value'] else math.inf
        criteria.append({'criterion': f'|Δk/k_ref| ≤ {limit}', 'inside': ratio <= limit,
                         'value': ratio, 'limit': limit})
        passed = passed and ratio <= limit
    if not criteria:
        return {'verdict': 'INCONCLUSIVE', 'reason': '容差未定义（sigma与relative至少给一项）'}
    return {'verdict': 'PASS' if passed else 'FAIL',
            'keff': {'mean': mean, 'std_dev': std},
            'reference_value': reference['value'],
            'deviation': deviation,
            'deviation_sigma': (deviation / sigma_combined) if sigma_combined else None,
            'relative_deviation': deviation / reference['value'] if reference['value'] else None,
            'sigma_combined': sigma_combined,
            'criteria': criteria}


def wait_terminal(python: str, env: dict, job_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = cli(python, 'openmc_job_status', {'job_id': job_id}, env)
        if last.get('state') in TERMINAL:
            return last
        time.sleep(1.0)
    return last or {'state': 'timeout'}


def run_benchmark(manifest_entry: dict, options: argparse.Namespace, env: dict) -> dict:
    name = manifest_entry['name']
    python = options.python
    case_dir_name = manifest_entry['case_dir']
    case_dir = Path(options.workspace) / case_dir_name
    run_spec = manifest_entry.get('run') or {}
    report = {'name': name, 'case_dir': case_dir_name, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    try:
        # 1. 生成或复用算例
        if case_dir.is_dir():
            report['case_prepare'] = {'reused': True}
        else:
            created = cli(python, 'openmc_create_model',
                          {'case_dir': case_dir_name,
                           'template': manifest_entry.get('template', 'synthetic-mgxs'),
                           **{key: run_spec[key] for key in ('particles', 'batches', 'inactive')
                              if key in run_spec}}, env)
            report['case_prepare'] = {'reused': False, 'files': created.get('files')}
        # 2. 核数据版本登记
        marker = {}
        if (case_dir / 'mc-case.json').is_file():
            marker = json.loads((case_dir / 'mc-case.json').read_text())
        xs_files = case_dir / 'materials.xml'
        cross_sections = None
        if xs_files.is_file():
            node = ET.parse(xs_files).getroot().find('cross_sections')
            cross_sections = node.text if node is not None else None
        registry = {'template': marker.get('template'), 'openmc_version': marker.get('openmc_version'),
                    'data_kind': marker.get('data_kind'), 'cross_sections': cross_sections,
                    'settings': settings_of(case_dir),
                    'settings_sha256': file_sha256(case_dir / 'settings.xml')}
        if cross_sections:
            xs_path = (case_dir / cross_sections) if not os.path.isabs(cross_sections) else Path(cross_sections)
            if xs_path.is_file():
                registry['cross_sections_sha256'] = file_sha256(xs_path)
        report['nuclear_data_registry'] = registry
        # 3. 参考值（先读：缺值或不可用不白跑作业；口径为“读参考值文件→相同设置运行→判定”）
        try:
            reference = resolve_reference(python, manifest_entry['reference'], case_dir,
                                          xs_python=runtime_python(env))
        except HarnessError as exc:
            report.update(verdict='INCONCLUSIVE', reason='参考值不可用：' + str(exc))
            return report
        report['reference'] = reference
        # 4. 比对前校验（结构+引擎解析）
        checked = cli(python, 'openmc_validate_inputs',
                      {'case_dir': case_dir_name, 'api_check': True}, env)
        problems = checked.get('problems') or []
        api = checked.get('openmc_api') or {}
        precheck_ok = bool(checked.get('structure_valid')) and not problems and bool(api.get('api_parsed'))
        report['precheck'] = {'ok': precheck_ok,
                              'validation_level': checked.get('validation_level'),
                              'api_parsed': api.get('api_parsed'),
                              'problems': problems}
        if not precheck_ok:
            report.update(verdict='INCONCLUSIVE', reason='比对前校验未通过：' + ('；'.join(map(str, problems)) or '引擎解析未通过'))
            return report
        # 5. 相同设置运行（清单run字段仅用于生成；已存在算例以现有XML为准并登记）
        submitted = cli(python, 'openmc_submit_job', {'case_dir': case_dir_name}, env)
        job_id = submitted['job_id']
        report['job_id'] = job_id
        status = wait_terminal(python, env, job_id, options.timeout)
        report['job_state'] = status.get('state')
        if status.get('state') != 'completed':
            report.update(verdict='INCONCLUSIVE',
                          reason='作业未完成：' + str(status.get('state')) + ('；' + str(status.get('error')) if status.get('error') else ''))
            return report
        # 6. 读取结果
        results = cli(python, 'openmc_read_results', {'job_id': job_id}, env)
        run = results.get('keff') or {}
        report['warnings'] = results.get('warnings') or []
        # 7. 判定
        report.update(judge(manifest_entry.get('quantity', 'keff'), run, reference,
                            manifest_entry.get('tolerance') or {}))
        return report
    except HarnessError as exc:
        report.update(verdict='INCONCLUSIVE', reason=str(exc))
        return report
    except subprocess.TimeoutExpired as exc:
        report.update(verdict='INCONCLUSIVE', reason='CLI调用超时：' + str(exc.cmd)[:200])
        return report


def render_markdown(reports: list) -> str:
    lines = ['| 基准 | 判定 | k_eff | 参考值 | 相对偏差 | 说明 |', '|---|---|---|---|---|---|']
    for item in reports:
        keff = item.get('keff') or {}
        mean = f"{keff['mean']:.6f}±{keff['std_dev']:.6f}" if keff else '—'
        ref = item.get('reference') if isinstance(item.get('reference'), dict) else {}
        value = ref.get('value')
        ref_text = f"{value:.6f}" if isinstance(value, (int, float)) else '—'
        rel = item.get('relative_deviation')
        rel_text = f"{rel:+.2e}" if isinstance(rel, (int, float)) else '—'
        note = item.get('reason') or ''
        if not note and item.get('criteria'):
            note = '；'.join(f"{c['criterion']} {'✓' if c['inside'] else '✗'}" for c in item['criteria'])
        lines.append(f"| {item['name']} | {item.get('verdict')} | {mean} | {ref_text} | {rel_text} | {note} |")
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='表4-2 准确率比对 harness：按参考值文件运行并判定')
    parser.add_argument('--manifest', default=str(DEFAULT_MANIFEST))
    parser.add_argument('--only', action='append', default=[],
                        help='仅运行指定名称的基准（可重复）；缺省运行全部')
    parser.add_argument('--workspace', default=os.environ.get('MC_WORKSPACE') or str(ROOT / 'workspace'))
    parser.add_argument('--python', default=str(DEFAULT_PYTHON), help='接口层解释器（跑mcp/cli.py）')
    parser.add_argument('--timeout', type=float, default=900.0, help='单个作业等待秒数')
    parser.add_argument('--json', action='store_true', help='输出JSON报告')
    parser.add_argument('--report', default=None, help='报告写入路径（可选）')
    options = parser.parse_args()
    manifest_path = Path(options.manifest)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    benchmarks = manifest.get('benchmarks') or []
    if not benchmarks:
        raise SystemExit('清单为空：' + str(manifest_path))
    if options.only:
        benchmarks = [item for item in benchmarks if item['name'] in options.only]
        if not benchmarks:
            raise SystemExit('--only未命中任何基准')
    env = dict(os.environ)
    env['MC_WORKSPACE'] = str(Path(options.workspace))
    reports = [run_benchmark(item, options, env) for item in benchmarks]
    summary = {'manifest': str(manifest_path), 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
               'tolerance_defaults': manifest.get('tolerance_defaults'), 'benchmarks': reports,
               'verdicts': {item['name']: item.get('verdict') for item in reports}}
    print(render_markdown(reports))
    if options.report:
        target = Path(options.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('\n报告已写入：' + str(target))
    if options.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    verdicts = summary['verdicts'].values()
    if 'FAIL' in verdicts:
        return 1
    if 'INCONCLUSIVE' in verdicts:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())