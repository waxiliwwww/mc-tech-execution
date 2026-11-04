"""Evidence-led diagnostics and a narrow, human-confirmed repair/recheck workflow."""
from collections import deque
import difflib
import fcntl
import json
from pathlib import Path
import re
import shutil
import time
import uuid
import xml.etree.ElementTree as ET

import business
import openmc_jobs as jobs
from openmc_runtime import Error, config, path_in_workspace, relative, workspace

RUN_MODES = ('eigenvalue', 'fixed source', 'plot', 'volume', 'particle restart')
RUN_MODE_SOURCE = 'openmc-0.16.0/io_formats/settings.rst'


def intact_inputs(directory, record):
    work = directory / 'work'
    for name, digest in record['input_files'].items():
        path = work / name
        if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(work.resolve()):
            raise Error('输入快照记录的路径无效')
        current = work
        for part in Path(name).parts:
            current = current / part
            if current.is_symlink():raise Error('输入快照不能包含符号链接')
        if not path.is_file() or jobs.file_hash(path) != digest:
            raise Error('原作业输入快照已改变，不能据此自动准备修复；请保留证据并重新核对来源')


def run_mode_reference():
    from openmc_knowledge import checked_file, read
    text, _info = checked_file(RUN_MODE_SOURCE)
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line == '``<run_mode>`` Element')
    return read(RUN_MODE_SOURCE, max(1, index), 10)


def diagnose_job(job_id: str):
    folder = jobs.job_directory(job_id); status = jobs.status(job_id); record = jobs.read(folder)
    if status['state'] not in jobs.TERMINAL:raise Error('计算仍在运行，先查询终态再诊断')
    intact_inputs(folder, record)
    findings = []
    rules = json.loads((business.ROOT / 'config/error-rules.json').read_text())
    log = folder / 'solver.log'
    if log.is_symlink():raise Error('日志不能是符号链接')
    tail = deque(maxlen=500); total_lines = 0
    if log.exists():
        with log.open(encoding='utf-8', errors='replace') as stream:
            for total_lines, line in enumerate(stream, 1):tail.append((total_lines, line[:4000]))
        tail_list = list(tail)
        for position, (number, line) in enumerate(tail_list):
            # 引擎会把一条消息折成多行（如 lost particle 的 "could not be located in any" + "cell"），
            # 逐行匹配会漏；这里把当前行与下一行拼起来再匹配，行号仍报首个命中行。
            window = line
            if position + 1 < len(tail_list):
                window = line.rstrip('\n') + ' ' + tail_list[position + 1][1].strip()
            for rule in rules:
                if re.search(rule['pattern'], line, re.IGNORECASE) or re.search(rule['pattern'], window, re.IGNORECASE):
                    findings.append({'source': 'solver.log', 'line': number, 'excerpt': window.strip()[:1000],
                                     'category': rule['category'], 'suggestion': rule['suggestion'],
                                     'rule_status': rule['status'], 'reference': rule.get('reference')})
    proposals = []
    for filename in (('model.xml',) if 'model.xml' in record['input_files'] else ('settings.xml',)):
        if filename not in record['input_files']:continue
        path = folder / 'work' / filename
        if path.stat().st_size > 2 * 1024 * 1024:
            findings.append({'source': filename, 'category': 'input_too_large', 'suggestion': '输入超过本次诊断范围，人工核对。'});continue
        text = path.read_text(encoding='utf-8')
        try:
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('不接受DTD/实体声明')
            root = ET.fromstring(text)
            settings = root if root.tag == 'settings' else root.find('settings')
            if settings is None:continue
            mode = settings.findtext('run_mode')
            if mode and mode.strip() not in RUN_MODES:
                reference = run_mode_reference()
                finding = {'source': filename, 'category': 'invalid_run_mode', 'value': mode.strip(),
                           'suggestion': '按官方run_mode枚举核对；仅唯一近似拼写可生成差异，须用户确认。', 'reference': reference}
                matches = list(re.finditer(r'<run_mode\b[^>]*>\s*([^<]+?)\s*</run_mode>', text))
                candidates = difflib.get_close_matches(mode.strip(), RUN_MODES, n=2, cutoff=.8)
                if len(matches) == 1 and len(candidates) == 1 and status['state'] == 'failed':
                    match = matches[0]; replacement = match.group(0).replace(match.group(1), candidates[0])
                    finding['line'] = text[:match.start()].count('\n') + 1
                    proposals.append({'file': filename, 'old_text': match.group(0), 'new_text': replacement,
                                      'category': 'run_mode_spelling', 'reference': reference})
                findings.append(finding)
            for field in ('particles', 'batches', 'inactive'):
                value = settings.findtext(field)
                if value is not None and int(value) < (0 if field == 'inactive' else 1):
                    findings.append({'source': filename, 'category': 'invalid_' + field, 'value': value,
                                     'suggestion': '需要用户给出期望计算设置，不自动猜测替换数值。'})
        except (ET.ParseError, ValueError) as error:
            findings.append({'source': filename, 'category': 'input_syntax', 'excerpt': str(error),
                             'suggestion': '核对输入语法；此类问题未自动生成替换。'})
    return {'job_id': job_id, 'state': status['state'], 'mode': record['mode'], 'source_case_dir': record['case_dir'],
            'findings': findings[:50], 'proposed_repairs': proposals,
            'diagnosis_status': 'evidence_found' if findings else 'unclassified',
            'log_lines_examined': len(tail), 'log_truncated_for_diagnosis': total_lines > len(tail),
            'provenance': record['provenance'], 'professional_acceptance': False,
            'note': '日志和输入检查仅提供证据及建议，未排除其他问题；原失败快照不修改。未知错误不报输入正确。'}


def prepare_repair(job_id: str, target_dir: str):
    diagnosis = diagnose_job(job_id)
    if diagnosis['state'] != 'failed':raise Error('仅从失败作业准备修复；其他状态请核对日志和输入')
    if diagnosis['mode'] != 'run':raise Error('自动修复闭环仅支持普通计算run；绘图/几何调试请保留原模式显式校核和重试，不自动改成普通计算')
    proposals = diagnosis['proposed_repairs']
    if len(proposals) != 1:raise Error('没有唯一且有依据的自动修复建议；需用户/专业人员确定修改，再用preview_repair生成差异')
    folder = jobs.job_directory(job_id); record = jobs.read(folder)
    intact_inputs(folder, record)
    target = path_in_workspace(target_dir, exists=False)
    if target.exists():raise Error('目标修复目录已存在，请选择新目录，保留原输入和历史')
    target.mkdir(parents=True)
    for name in record['input_files']:
        destination = target / name; destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / 'work' / name, destination)
        if jobs.file_hash(destination) != record['input_files'][name]:raise Error('复制期间输入改变，未生成修复计划')
    proposal = proposals[0]
    context = {'origin_job_id': job_id, 'case_dir': target_dir, 'mode': 'run', 'input_files': record['input_files'],
               'diagnosis': diagnosis, 'created_at': time.time()}
    plan = business.preview_repair(relative(target / proposal['file']), proposal['old_text'], proposal['new_text'], _context=context)
    return {'origin_job_id': job_id, 'case_dir': target_dir, 'plan': plan, 'source_unchanged': True,
            'next': '展示plan.diff，取得真人确认后调用apply_repair；之后调用openmc_recheck_repair。'}


def recheck_repair(plan_id: str, threads: int = 1):
    if type(threads) is not int or not 1 <= threads <= 64:raise Error('threads须为1..64')
    # Read/validate ID before constructing an internal record path.
    public = business.read_repair_plan(plan_id)
    path = workspace() / '.mc-plans' / (plan_id + '.json')
    with path.with_suffix('.recheck.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = json.loads(path.read_text())
        if plan['state'] != 'applied':raise Error('尚未应用已确认的修复，不能跳过用户确认直接重算')
        context = plan.get('context')
        if not context:raise Error('此计划没有失败作业关联；请使用普通校核和计算接口显式验证')
        if context.get('mode') != 'run':raise Error('此闭环仅支持普通计算run，不能改变原作业模式')
        if context.get('rerun_job_id'):
            identifier = context['rerun_job_id']
            if not (workspace() / '.mc-jobs' / identifier / 'job.json').exists():
                raise Error('已有重算编号但提交未被确认；保留记录，不能自动重复提交，请人工核对')
            return {'plan_id': plan_id, 'origin_job_id': context['origin_job_id'], 'validation': context.get('validation'),
                    'job': jobs.status(identifier), 'reused_submission': True, 'next': '继续按同一job_id查询/读取结果，不重新提交。'}
        target = path_in_workspace(context['case_dir'])
        repaired = business._path(plan['path'])
        for name, digest in context['input_files'].items():
            file = target / name
            checked = business._path(relative(file))
            expected = plan['after_sha256'] if checked == repaired else digest
            if jobs.file_hash(checked) != expected:raise Error('修复后的输入再次改变，请重新核对和生成计划；未提交计算')
        actual = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
        if actual != set(context['input_files']):raise Error('修复目录的文件集合已改变，请重新核对；未提交计算')
        from openmc_tools import environment, validate_inputs
        validation = validate_inputs(context['case_dir'])
        if validation['structure_valid']:
            from openmc_runtime import api
            try:
                validation['openmc_api'] = api('validate', target, {})
                validation['validation_level'] = 'xml_and_openmc_model_parser'
            except Error as error:
                validation['api_error'] = str(error)
        context['validation'] = validation
        if not validation['structure_valid'] or validation.get('api_error'):
            business._write_json(path, plan)
            return {'plan_id': plan_id, 'origin_job_id': context['origin_job_id'], 'validation': validation,
                    'job': None, 'next': '校核未通过，没有提交重算；核对problems并提出新修复。'}
        runtime = environment()
        if not runtime['executable_available'] or not runtime['version_matches']:raise Error('运行环境不符，没有提交重算')
        identifier = uuid.uuid4().hex
        context.update(rerun_job_id=identifier, recheck_started_at=time.time())
        business._write_json(path, plan)
        # Reserve before launch: a lost response/restarted service must not duplicate a run.
        expected_inputs = dict(context['input_files'])
        expected_inputs[repaired.relative_to(target).as_posix()] = plan['after_sha256']
        submitted = jobs.submit(context['case_dir'], threads=threads, _job_id=identifier, _expected_inputs=expected_inputs)
        return {'plan_id': plan_id, 'origin_job_id': context['origin_job_id'], 'validation': validation,
                'job': submitted, 'reused_submission': False,
                'next': '修改、校核和计算完成分别判断；查询此job_id终态后再读取结果，失败继续保留。'}
