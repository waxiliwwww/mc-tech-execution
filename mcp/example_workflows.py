"""Stepwise example instances use the existing owned-worker job controls."""
from __future__ import annotations
import fcntl
import json
import shutil
from pathlib import Path
import uuid
import openmc_jobs as jobs
from official_catalog import definition, describe, verify_bundled_sources
from openmc_runtime import ROOT, Error, atomic_json, config, path_in_workspace, relative, provenance


def prepare_official_example(example_id: str, case_dir: str, parameters: dict | None = None) -> dict:
    item = definition(example_id)
    if item.get('scope')=='deferred':raise Error('该例题已按用户决定暂缓，不在本轮范围：'+item['id'])
    parameters = {} if parameters is None else parameters
    if not isinstance(parameters, dict):
        raise Error('parameters须为对象')
    settings_keys = {'particles', 'batches', 'inactive', 'seed'}
    allowed = settings_keys | {'factory', 'mgxs_file'}
    if set(parameters) - allowed or any(type(v) is not int for k,v in parameters.items() if k in settings_keys):
        raise Error('当前运行设置仅接受整数particles/batches/inactive/seed；省略保留官方原值')
    for key, value in parameters.items():
        if key not in settings_keys:continue
        if value < (0 if key == 'inactive' else 1):
            raise Error('参数范围无效：' + key)
    factory=parameters.get('factory',{})
    schema=item.get('factory_parameters',{})
    if not isinstance(factory,dict) or set(factory)-set(schema):
        raise Error('工厂参数不支持；使用examples show查看该例题公开参数')
    for key,value in factory.items():
        expected=schema[key]['type']
        if ((expected=='boolean' and type(value) is not bool) or
            (expected=='integer' and (type(value) is not int or value<1)) or
            (expected=='array' and (not isinstance(value,list) or not all(isinstance(x,str) and x for x in value)))):
            raise Error('工厂参数类型/范围无效：'+key)
    supplied=None
    if 'mgxs_file' in item.get('required_inputs',{}):
        if not parameters.get('mgxs_file'):raise Error('该官方工厂需要多群截面输入：parameters.mgxs_file；详见examples show')
        supplied=path_in_workspace(parameters['mgxs_file'])
        if not supplied.is_file():raise Error('mgxs_file须为工作区内的HDF5文件')
        with supplied.open('rb') as f:
            if f.read(8)!=b'\x89HDF\r\n\x1a\n':raise Error('mgxs_file不是HDF5')
        if factory.get('mat_names') is not None and len(factory['mat_names'])!=factory.get('num_regions',1):
            raise Error('mat_names数量须等于num_regions')
    elif 'mgxs_file' in parameters:raise Error('该例题不接受mgxs_file')
    if item['source_group'] == 'notebook':
        from official_sources import verify_notebook_sources
        source = verify_notebook_sources(item['source_file'])
        if parameters:raise Error('Notebook设置须按逐例参数说明调整；当前不接受通用覆盖，以免静默忽略。')
    else:
        source = verify_bundled_sources(item)
    from nuclear_data import initialize_index
    runtime = config()
    runtime.setdefault('nuclear_data_library', 'endfb-vii.1')
    initialize_index(runtime)
    target = path_in_workspace(case_dir, exists=False)
    if target.exists():
        raise Error('目标目录已存在；使用新目录，保留已有输入与计算')
    if item['workflow'] not in ('transport', 'random_ray', 'compiled_source', 'notebook', 'depletion', 'depletion_restart', 'pulsed_source'):
        raise Error('该官方例题的专用工作流尚未适配，不能用普通计算代替：' + item['workflow'])
    target.mkdir(parents=True)
    if item['source_group'] == 'source':
        shutil.copytree((source / item['source_file']).parent, target, dirs_exist_ok=True)
    if item['source_group'] == 'notebook':
        for name in (item['source_file'], 'c5g7.h5', 'chain_simple.xml'):
            shutil.copy2(source/name,target/name)
        shutil.copytree(source/'images',target/'images')
    if supplied:shutil.copy2(supplied,target/'2g.h5')
    example_id_internal = uuid.uuid4().hex
    record_dir = path_in_workspace('.mc-examples/' + example_id_internal, exists=False, internal=True)
    record_dir.mkdir(parents=True)
    record = {'instance_id': example_id_internal, 'example_id': example_id, 'case_dir': case_dir,
              'parameters': parameters, 'runtime': runtime, 'source_revision': item['source_revision'], 'steps': {},
              'current_case_dir': case_dir, 'state': 'prepared',
              'source_files': {p.relative_to(target).as_posix(): jobs.file_hash(p)
                               for p in target.rglob('*') if p.is_file()}}
    atomic_json(target / 'mc-case.json', {'data_kind': 'public_official_example', 'template': example_id,
                'source_url': item['source_url'], 'source_revision': item['source_revision'],
                'source_sha256': jobs.file_hash(source / item['source_file']),
                'note': '固定官方例题；运行设置仅在显式parameters中调整。未作甲方专业验收。'})
    record['initial_files'] = {p.relative_to(target).as_posix(): jobs.file_hash(p)
                               for p in target.rglob('*') if p.is_file()}
    atomic_json(record_dir / 'example.json', record)
    return example_status(example_id_internal)


def instance_directory(instance_id: str) -> Path:
    import re
    if not isinstance(instance_id, str) or not re.fullmatch('[a-f0-9]{32}', instance_id):
        raise Error('无效instance_id；使用准备官方例题返回的编号')
    directory = path_in_workspace('.mc-examples/' + instance_id, internal=True)
    if not (directory / 'example.json').is_file():
        raise Error('官方例题实例不存在')
    return directory


def _read(directory: Path) -> dict:
    return json.loads((directory / 'example.json').read_text())


def example_status(instance_id: str, wait_seconds: int = 0) -> dict:
    import time
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 30:
        raise Error('wait_seconds须为0..30秒；默认立即查询，等待超时不代表计算失败')
    deadline=time.monotonic()+wait_seconds
    while True:
        value=_example_status_once(instance_id)
        if value['state']!='running' or time.monotonic()>=deadline:return value
        time.sleep(min(.25,max(0,deadline-time.monotonic())))


def _example_status_once(instance_id: str) -> dict:
    directory = instance_directory(instance_id)
    with (directory / 'record.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = _read(directory)
        for step, item in value['steps'].items():
            try:
                item.update(jobs.status(item['job_id']))
            except Error:
                item.update(state='interrupted', error='步骤提交记录不完整；保留预留编号，不自动重复执行。')
        completed = [s['id'] for s in describe(value['example_id'])['steps']
                     if value['steps'].get(s['id'], {}).get('state') == 'completed']
        pipeline = describe(value['example_id'])['steps']
        next_step = next((s for s in pipeline if s['id'] not in completed), None)
        if completed:
            last = value['steps'][completed[-1]]
            record = jobs.read(jobs.job_directory(last['job_id']))
            value['current_case_dir'] = record['work_dir']
        value['next_step'] = next_step
        states = {x.get('state') for x in value['steps'].values()}
        value['state'] = ('running' if states - jobs.TERMINAL else
                          'failed' if states & {'failed', 'interrupted'} else
                          'cancelled' if 'cancelled' in states else
                          'completed' if next_step is None else 'prepared')
        if value['state'] in jobs.TERMINAL:
            ends=[j['finished_at'] for j in value['steps'].values() if j.get('finished_at')]
            if ends:value['finished_at']=max(ends)
        atomic_json(directory / 'example.json', value)
    public = {k: v for k, v in value.items() if k not in
              ('runtime', 'source_files', 'initial_files', 'current_case_dir')}
    public['scope'] = definition(value['example_id']).get('scope','included')
    public['runtime_summary'] = {k: value.get('runtime', {}).get(k)
                                for k in ('expected_version', 'nuclear_data_library')}
    if next_step is None:
        summary = f"{value['state']}：全部 {len(pipeline)} 步完成，可读取结果"
    else:
        summary = (f"{value['state']}：已完成 {len(completed)}/{len(pipeline)} 步；"
                   f"下一步 {next_step['name']}（{next_step['id']}）")
    return {'human_summary': summary, **public,
            'case_server_path': str(path_in_workspace(value['case_dir'])),
            'steps_available': pipeline, 'professional_acceptance': False,
            'next': None if next_step is None else {'tool': 'openmc_run_example_step',
                   'arguments': {'instance_id': instance_id, 'step': next_step['id']}}}


def run_example_step(instance_id: str, step: str, threads: int = 1) -> dict:
    # Refresh transitions before the lock, then re-read under the per-instance lock.
    example_status(instance_id)
    directory = instance_directory(instance_id)
    with (directory / 'record.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = _read(directory)
        existing = value['steps'].get(step)
        if existing:
            try:
                current = jobs.status(existing['job_id'])
            except Error:
                current = {'job_id': existing['job_id'], 'state': 'interrupted',
                           'error': '步骤提交记录不完整；不自动重提。'}
            return {'instance_id': instance_id, 'step': step, **current, 'reused': True,
                    'note': '保留已有步骤记录，不重复提交；失败后用新实例保留原失败。'}
        if definition(value['example_id']).get('scope')=='deferred':
            raise Error('该例题已按用户决定暂缓，未提交新步骤')
        next_step = value.get('next_step')
        if next_step is None or step != next_step['id']:
            raise Error('步骤顺序无效；先查询实例，完成返回的next_step')
        if not next_step['implemented']:
            raise Error('该步骤尚未适配，不能报告执行成功：' + step)
        if type(threads) is not int or not 1 <= threads <= 64:
            raise Error('threads须为1..64')
        item = definition(value['example_id'])
        if item['source_group'] == 'notebook':
            from official_sources import verify_notebook_sources
            verify_notebook_sources(item['source_file'])
        else:
            verify_bundled_sources(item)
        previous = [value['steps'][s['id']] for s in describe(item['id'])['steps']
                    if s['id'] in value['steps']]
        expected = value['initial_files']
        if previous:
            record = jobs.read(jobs.job_directory(previous[-1]['job_id']))
            expected = {**record['input_files'], **record.get('output_files', {})}
            work = path_in_workspace(record['work_dir'], internal=True)
            for name, digest in expected.items():
                path = work / name
                if path.is_symlink() or not path.is_file() or jobs.file_hash(path) != digest:
                    raise Error('前置步骤的输入/产物已改变，未启动下一步：' + name)
        current = path_in_workspace(value['current_case_dir'], internal=True)
        for name, digest in value['source_files'].items():
            path = current / name
            if path.is_symlink() or not path.is_file() or jobs.file_hash(path) != digest:
                raise Error('固定官方源码/资源被修改，未执行：' + name)
        workflow = None if step == 'compute' else {'example_id': item['id'], 'step': step,
                    'parameters': value['parameters']}
        reserved_job = uuid.uuid4().hex
        value['steps'][step] = {'job_id': reserved_job, 'state': 'submitting'}
        atomic_json(directory / 'example.json', value)
        started = jobs.submit(value['current_case_dir'], threads=threads, _internal=True,
                              _example_workflow=workflow, _job_id=reserved_job,
                              _expected_inputs=expected, _preserve_outputs=True,
                              _runtime=value.get('runtime'))
        atomic_json(directory / 'example.json', value)
    return {'instance_id': instance_id, 'step': step, **started, 'reused': False,
            'next': {'tool': 'openmc_example_status', 'arguments': {'instance_id': instance_id}}}


def cancel_example(instance_id: str) -> dict:
    value = example_status(instance_id)
    cancelled = []
    for step, job in value['steps'].items():
        if job['state'] not in jobs.TERMINAL:
            cancelled.append({'step': step, **jobs.cancel(job['job_id'])})
    return {'instance_id': instance_id, 'cancellations': cancelled,
            'note': '取消仅针对本实例正在运行的步骤；查询实例终态确认。'}


def read_example_results(instance_id: str, calculation_id: str | None = None, max_values: int = 1000) -> dict:
    from openmc_artifacts import list_artifacts
    from openmc_tools import read_results
    if type(max_values) is not int or not 1 <= max_values <= 10000:
        raise Error('max_values须为1..10000；只影响返回预览，不改变StatePoint或图表数据')
    value = example_status(instance_id)
    artifacts = []
    for step, job in value['steps'].items():
        artifacts.append({'step': step, **list_artifacts('job', job['job_id'])})
    result = None
    notebook = None
    job = value['steps'].get('compute')
    if job and job['state'] == 'completed':
        result = read_results(job['job_id'], max_values=max_values)
        result['statepoint_server_path'] = str(path_in_workspace(result['statepoint'], internal=True))
    executed = value['steps'].get('execute')
    if executed:
        directory = jobs.job_directory(executed['job_id']);record = jobs.read(directory)
        summary = directory / 'work/notebook-result.json'
        expected = record.get('output_files',{}).get('notebook-result.json')
        if expected:
            if summary.is_symlink() or not summary.is_file() or jobs.file_hash(summary)!=expected:
                raise Error('Notebook结果摘要已改变，不能继续报告原结果')
            notebook=json.loads(summary.read_text())
            notebook['report_server_path']=str(summary.parent/notebook['report'])
    selected_calculation=None
    if calculation_id is not None:
        import re
        if not isinstance(calculation_id,str) or not re.fullmatch('[0-9]{4}',calculation_id):
            raise Error('calculation_id须为results列出的四位求解阶段编号')
        if not executed or executed['state']!='completed' or not notebook:
            raise Error('选择求解阶段需要已完成并固化的Notebook作业')
        directory=jobs.job_directory(executed['job_id']);record=jobs.read(directory);work=directory/'work'
        def verified(name,digest=None):
            path=work/name
            if not path.resolve().is_relative_to(work.resolve()) or any(p.is_symlink() for p in [path,*path.parents] if p!=work.parent):
                raise Error('求解阶段路径无效')
            sealed=record.get('output_files',{}).get(name)
            if not sealed or (digest and sealed!=digest) or not path.is_file() or jobs.file_hash(path)!=sealed:
                raise Error('求解阶段产物缺失或已改变，未读取：'+name)
            return path
        index=json.loads(verified('notebook-stages.json').read_text())
        stage=next((s for s in index['calculations'] if s['id']==calculation_id),None)
        if not stage or stage['state']!='completed':raise Error('未找到已完成的求解阶段')
        for metadata in stage.get('inputs',{}).values():verified(metadata['path'],metadata['sha256'])
        points=[]
        for name,metadata in stage['outputs'].items():
            path=verified(metadata['path'],metadata['sha256'])
            if metadata.get('role')=='statepoint' or (name.startswith('statepoint.') and name.endswith('.h5')):points.append(path)
        if not points:raise Error('该阶段没有StatePoint；使用results中的Notebook输出和实际文件')
        point=max(points,key=lambda p: (int(p.name.split('.')[1]) if p.name.startswith('statepoint.') else -1,p.name))
        from openmc_runtime import api
        data=api('results',point.parent,{'statepoint':point.name,'max_values':max_values},record['runtime'])
        selected_calculation={**data,'calculation_id':calculation_id,'origin_job_id':executed['job_id'],
                              'statepoint_server_path':str(point),'statepoint_sha256':jobs.file_hash(point),
                              'original_cell_index':stage['original_cell_index'],
                              'display_note':'这是所选求解阶段的原始数据；图表/报告请查看本Notebook按实际读取关系生成的原生报告，不能把origin_job_id当作通用绘图的所选阶段。',
                              'input_server_paths':{name:str(work/meta['path']) for name,meta in stage.get('inputs',{}).items()},
                              'professional_acceptance':False}
    script_results=[]
    for stage, job in value['steps'].items():
        directory=jobs.job_directory(job['job_id']);record=jobs.read(directory)
        name='script-result-'+stage+'.json';summary=directory/'work'/name
        expected=record.get('output_files',{}).get(name)
        if expected:
            if summary.is_symlink() or not summary.is_file() or jobs.file_hash(summary)!=expected:
                raise Error('多阶段结果摘要已改变，不能报告原结果')
            script_results.append({**json.loads(summary.read_text()),'summary_server_path':str(summary)})
    return {'instance_id': instance_id, 'example_id': value['example_id'], 'state': value['state'],
            'max_values_per_tally': max_values,
            'script_results':script_results, 'calculation': selected_calculation or result, 'notebook': notebook, 'stage_artifacts': artifacts,
            'professional_acceptance': False,
            'note': '每个tally只返回前max_values个统计值，values_truncated/total_values/returned_values标明预览范围；完整数据保存在StatePoint中，绘图不使用截断预览。按步骤保存输入与产物；未定义计分的例题只返回其实际计算量。'}
