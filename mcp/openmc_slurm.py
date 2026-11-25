"""Opt-in Slurm adapter. No network traffic or local configuration edits on import.

Prepare is offline. Submit/status/cancel/collect use the explicitly configured
login-node transport. Controller fixtures never count as target-cluster evidence.
"""
from __future__ import annotations
import base64
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

import openmc_jobs as jobs
from openmc_runtime import ROOT, Error, atomic_json, config, path_in_workspace, provenance, relative, workspace

TERMINAL = {'completed', 'failed', 'cancelled'}
HELPER = Path(__file__).with_name('slurm_remote.py')
CONTROL = {'.forge-input.json', '.forge-result.json', 'forge-job.sh', 'forge-runner.py'}


def _argv(value, key):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x or '\0' in x for x in value):
        raise Error(key + '须为管理员配置的非空命令参数数组')
    return value


def profile():
    source = os.environ.get('MC_SLURM_CONFIG')
    if not source: raise Error('Slurm尚未配置：无需自备脚本，工具有内置默认模板；仍需确认集群入口/工作目录及OpenMC环境。可复制config/slurm.example.json到私有位置并设置MC_SLURM_CONFIG；不会猜测站点或修改现有OpenMC配置')
    try: p = json.loads(Path(source).read_text())
    except (OSError, ValueError) as exc: raise Error('MC_SLURM_CONFIG无法读取') from exc
    if p.get('transport') not in ('ssh', 'local'): raise Error('transport须为ssh或local（登录节点）')
    root = p.get('remote_root', '')
    if not isinstance(root, str) or not root.startswith('/') or '..' in PurePosixPath(root).parts or any(c in root for c in '\r\n\0'):
        raise Error('remote_root须为集群专用绝对工作目录')
    if root == '/': raise Error('remote_root不能为根目录')
    if p['transport'] == 'ssh':
        host = p.get('host')
        if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_@.:-]+', host) or host.startswith('-'):
            raise Error('host须为SSH配置别名或user@host；凭据不通过工具参数传递')
        _argv(p.setdefault('ssh_command', ['ssh', '-o', 'BatchMode=yes']), 'ssh_command')
    _argv(p.setdefault('python_command', ['python3']), 'python_command')
    _argv(p.setdefault('openmc_command', ['openmc']), 'openmc_command')
    if p.get('mpi_command') is not None:
        _argv(p['mpi_command'], 'mpi_command')
        if any(v in ('-n','-np','--np','--ntasks') or v.startswith(('--ntasks=','--np=')) for v in p['mpi_command']):
            raise Error('mpi_command只填写已确认的启动器和固定选项，进程数统一填写resources.ntasks，不要重复指定')
    if not isinstance(p.get('resource_defaults', {}), dict):raise Error('resource_defaults须为资源对象')
    commands = p.setdefault('commands', {})
    for key in ('sbatch', 'squeue', 'sacct', 'scancel', 'scontrol'): _argv(commands.setdefault(key, [key]), key)
    for key, default in [('command_timeout_seconds', 30), ('transfer_timeout_seconds', 600), ('max_transfer_bytes', 2*1024**3)]:
        if type(p.setdefault(key, default)) is not int or p[key] <= 0: raise Error(key + '须为正整数')
    setup = p.setdefault('environment_setup', [])
    if not isinstance(setup, list) or any(not isinstance(v, str) or '\0' in v for v in setup): raise Error('environment_setup须为管理员shell语句列表')
    mappings = p.setdefault('path_mappings', {})
    if not isinstance(mappings, dict) or any(not isinstance(a, str) or not isinstance(b, str) or not b.startswith('/') or any(c in b for c in '\r\n\0') for a,b in mappings.items()):
        raise Error('path_mappings须为原引用到集群绝对路径的映射')
    for key in ('cross_sections',):
        if key in p and (not isinstance(p[key], str) or not p[key].startswith('/') or any(c in p[key] for c in '\r\n\0')):
            raise Error(key + '须为集群绝对路径')
    return p


def directory(submission_id):
    if not isinstance(submission_id, str) or not re.fullmatch('[a-f0-9]{32}', submission_id): raise Error('submission_id须为prepare返回的32位编号')
    d = path_in_workspace('.mc-slurm/' + submission_id, internal=True)
    if not (d/'job.json').is_file(): raise Error('Slurm准备记录不存在')
    return d


@contextmanager
def locked(submission_id):
    d = directory(submission_id)
    with (d/'record.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield d, jobs.read(d)


def _summary(record):
    return {k: record.get(k) for k in ('submission_id','name','case_dir','state','cluster_job_id','remote_dir',
            'scheduler_state','scheduler_exit_code','scheduler_reason','submitted_at','collected_job_id','cancel_requested','last_error')}


def _persist(d, r): atomic_json(d/'job.json', r)


def _remote(p, action, payload, input_path=None, output_path=None):
    payload = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    cmd = p['python_command'] + ['-c', HELPER.read_text(), action, payload]
    if p['transport'] == 'ssh': cmd = p['ssh_command'] + ['--', p['host'], shlex.join(cmd)]
    timeout = p['transfer_timeout_seconds'] if action in ('stage','fetch') else p['command_timeout_seconds'] + 5
    with tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as capture:
        source = open(input_path, 'rb') if input_path else subprocess.DEVNULL
        output = open(output_path, 'wb') if output_path else capture
        try:
            try:
                process = subprocess.Popen(cmd, stdin=source, stdout=output, stderr=err, start_new_session=True)
                try:
                    process.wait(timeout=timeout)
                    result = process
                finally:
                    # Only this transport's process group; never signal by name/user.
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait()
            except (OSError, subprocess.TimeoutExpired) as exc: raise Error('Slurm连接/传输失败或超时（'+type(exc).__name__+'）；提交结果可能未知，先查询原submission_id，不要重新prepare提交') from exc
            if result.returncode:
                err.seek(0); message = err.read(4000).decode(errors='replace')
                raise Error('Slurm远端操作失败：' + message)
            if output_path: return None
            capture.seek(0); raw = capture.read(2*1024**2+1)
            if len(raw)>2*1024**2: raise Error('Slurm响应过大')
            try: return json.loads(raw)
            except ValueError as exc: raise Error('Slurm远端响应不是JSON，保留记录并检查登录节点启动输出') from exc
        finally:
            if input_path: source.close()
            if output_path: output.close()


def _command(p, directory, key, args):
    try:value = _remote(p, 'command', {'directory':directory,'argv':p['commands'][key]+args,'timeout':p['command_timeout_seconds']})
    except Error as exc:raise Error(key+'失败：'+str(exc)) from exc
    if value['returncode']: raise Error(key + '失败：' + value['stderr'][:2000])
    return value['stdout']


def _resources(resources, defaults=None):
    r = {'nodes':1,'ntasks':1,'cpus_per_task':1,'time':'00:10:00', **(defaults or {}), **(resources or {})}
    if set(r)-{'nodes','ntasks','cpus_per_task','time','memory_mb','partition','account','qos'}: raise Error('未知资源字段')
    for key in ('nodes','ntasks'):
        if type(r[key]) is not int or r[key]<1:raise Error(key+'须为正整数')
    if type(r['cpus_per_task']) is not int or r['cpus_per_task']<1: raise Error('cpus_per_task须为正整数')
    if 'memory_mb' in r and (type(r['memory_mb']) is not int or r['memory_mb']<1): raise Error('memory_mb须为正整数')
    if not isinstance(r['time'],str) or not re.fullmatch(r'(?:\d+(?::[0-5]\d(?::[0-5]\d)?)?|\d+-\d{1,2}(?::[0-5]\d(?::[0-5]\d)?)?)',r['time']): raise Error('time须为Slurm时间格式，如分钟数、HH:MM:SS或D-HH:MM:SS；上限按队列确认')
    for k in ('partition','account','qos'):
        if k in r and (not isinstance(r[k],str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',r[k]) or r[k].startswith('-')): raise Error(k+'须为集群资源名称，不含空白或控制字符')
    return r


def _is_hdf5(path):
    with path.open('rb') as stream:return stream.read(8)==b'\x89HDF\r\n\x1a\n'


def _map_paths(snapshot, p):
    changes=[]
    for file in snapshot.glob('*.xml'):
        if file.name not in ('model.xml','geometry.xml','materials.xml','settings.xml','tallies.xml','plots.xml'): continue
        tree=ET.parse(file); changed=False
        for node in tree.getroot().iter():
            if node.tag=='output':
                value=node.get('path') or node.findtext('path')
                if value and value.strip() not in ('.','./'): raise Error('HPC输入output.path须指向作业目录；先整理模型，未提交')
            if node.tag=='source' and (node.get('library') or node.findtext('library') or node.get('type')=='compiled'):
                raise Error('compiled source需集群专门配置，本Slurm路径不执行用户动态库')
            refs=[]
            if node.tag in ('cross_sections','filename','file','path','library') and node.text and node.text.strip(): refs.append((None,node.text.strip()))
            refs += [(key,node.get(key)) for key in ('filename','file','path','library') if node.get(key)]
            for key,value in refs:
                if node.tag=='output' or value in ('.','./'): continue
                mapped=p['path_mappings'].get(value)
                if mapped:
                    if key:node.set(key,mapped)
                    else:node.text=mapped
                    changes.append({'file':file.name,'from':value,'to':mapped});changed=True
                elif Path(value).is_absolute():
                    raise Error(file.name+'外部引用缺少path_mappings：'+value+'；请明确集群对应路径，不复制整个核数据库')
                elif '..' in Path(value).parts or not (snapshot/value).is_file():
                    raise Error(file.name+'依赖未包含于模型目录：'+value)
                elif node.tag=='cross_sections' and not _is_hdf5(snapshot/value):
                    # An index may reference files outside the model directory.
                    raise Error('核数据索引须用path_mappings明确集群路径；不把仅复制索引当库已搬移')
        if changed:tree.write(file,encoding='utf-8',xml_declaration=True)
    return changes


def _script(remote_dir, p, r, identifier, log_limit, name=None):
    lines=['#!/usr/bin/env bash', '#SBATCH --job-name='+shlex.quote(name or 'openmc'),
           '#SBATCH --comment=forge-'+identifier, '#SBATCH --nodes='+str(r['nodes']),
           '#SBATCH --ntasks='+str(r['ntasks']), '#SBATCH --cpus-per-task='+str(r['cpus_per_task']),
           '#SBATCH --time='+r['time'], '#SBATCH --output=slurm-%j.out', '#SBATCH --error=slurm-%j.err']
    for key,flag in [('memory_mb','mem'),('partition','partition'),('account','account'),('qos','qos')]:
        if key in r:lines.append('#SBATCH --'+flag+'='+str(r[key]))
    lines += ['set -euo pipefail','cd -- '+shlex.quote(remote_dir)]
    if p.get('cross_sections'):lines += ['export OPENMC_CROSS_SECTIONS='+shlex.quote(p['cross_sections'])]
    lines += p['environment_setup']
    lines += ['export OMP_NUM_THREADS='+str(r['cpus_per_task'])]
    version=shlex.join(p['openmc_command']+['--version'])
    pattern=r'(^|[^0-9.])'+re.escape(config()['expected_version'])+r'([^0-9.]|$)'
    lines += [version+' > .forge-version.txt 2>&1 || { cat .forge-version.txt | tee solver.log; exit 2; }',
              'grep -Eq '+shlex.quote(pattern)+' .forge-version.txt || { echo 远端OpenMC版本不符或无法核实 | tee solver.log; cat .forge-version.txt | tee -a solver.log; exit 2; }']
    command=p['openmc_command']+['-s',str(r['cpus_per_task'])]
    if r['ntasks']>1:command=p['mpi_command']+['-n',str(r['ntasks'])]+command
    # OpenMC runs directly; only small exit/hash records follow the command.
    # Drain logs after the cap so a full log never kills an otherwise valid run.
    lines += ['set +e',shlex.join(command)+' 2>&1 | { head -c '+str(log_limit)+' | tee solver.log || { cat >/dev/null; exit 1; }; extra=$(wc -c); if (( extra > 0 )); then : > .forge-log-truncated; fi; }',
              'codes=("${PIPESTATUS[@]}")', 'solver_code=${codes[0]}',
              'if (( solver_code == 0 && codes[1] != 0 )); then solver_code=${codes[1]}; fi', 'set -e',
              "find . -type f ! -name '.forge-*' ! -name 'slurm-*.out' ! -name 'slurm-*.err' -print0 | sort -z | xargs -0 -r sha256sum -z > .forge-files.sha256.tmp",
              'mv -- .forge-files.sha256.tmp .forge-files.sha256',
              "printf '%s\n' \"$solver_code\" > .forge-exit-code.tmp",
              'mv -- .forge-exit-code.tmp .forge-exit-code', 'exit "$solver_code"']
    return '\n'.join(lines)+'\n'


def prepare_slurm(case_dir: str, resources: dict | None = None, name: str | None = None) -> dict:
    p=profile();r=_resources(resources,p.get('resource_defaults'));name=jobs.checked_name(name)
    if r['ntasks']>1 and not p.get('mpi_command'):
        raise Error('本次要求多个MPI进程；请先确认OpenMC支持MPI，并在站点配置mpi_command填写已确认的启动器（如["mpirun"]或["srun"]）。没有现场脚本也可使用内置单进程模板；不会猜测MPI环境。')
    from openmc_tools import validate_inputs, require_semantic_submission, _semantic_summary
    checked=validate_inputs(case_dir, semantic_check=True)
    if not checked['structure_valid']:raise Error('输入结构检查失败：'+str(checked['problems']))
    require_semantic_submission(checked.get('semantic') or {})
    source=path_in_workspace(case_dir)
    if source==workspace():raise Error('请选择具体模型子目录')
    for path in source.rglob('*'):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):raise Error('输入包含符号链接或特殊文件')
        if path.name in CONTROL or path.name.startswith('.forge-'):raise Error('算例包含Slurm控制文件；请选择纯模型输入')
    reference_script=({'path':str(source/'in.slurm'),'sha256':jobs.file_hash(source/'in.slurm'),
                       'executed':False,'note':'源in.slurm仅作可选参考，原文件保留；快照使用工具按当前站点配置生成的新脚本，不自动采用参考设置。'}
                      if (source/'in.slurm').is_file() else None)
    cfg=config()
    from openmc_storage import check_submission_space
    disk=check_submission_space(source, workspace(), cfg, False, jobs.is_old_output)
    identifier=uuid.uuid4().hex
    root=path_in_workspace('.mc-slurm',exists=False,internal=True);root.mkdir(exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.preparing-',dir=root));snapshot=staging/'input'
    try:
        shutil.copytree(source,snapshot,ignore=lambda d,n:[x for x in n if (Path(d)==source and x=='in.slurm') or jobs.is_old_output(x) or x in ('views','__pycache__','solver.log','worker.log') or (x.startswith('slurm-') and x.endswith(('.out','.err')))])
        # Recheck the actual snapshot with the same admission rule as local jobs.
        require_semantic_submission(_semantic_summary(snapshot,[x.name for x in snapshot.glob('*.xml')],cfg))
        original={str(x.relative_to(snapshot)):jobs.file_hash(x) for x in snapshot.rglob('*') if x.is_file()}
        changes=_map_paths(snapshot,p)
        remote_dir=str(PurePosixPath(p['remote_root'])/identifier)
        script=_script(remote_dir,p,r,identifier,cfg['max_log_bytes'],name)
        (snapshot/'in.slurm').write_text(script)
        inputs={str(x.relative_to(snapshot)):jobs.file_hash(x) for x in snapshot.rglob('*') if x.is_file()}
        size=sum(x.stat().st_size for x in snapshot.rglob('*') if x.is_file())
        if size>p['max_transfer_bytes']:raise Error('输入超过Slurm配置max_transfer_bytes；调整部署配置或整理依赖')
        record={'submission_id':identifier,'case_dir':case_dir,'name':name,'state':'prepared','resources':r,
                'remote_dir':remote_dir,'profile':p,'batch_script_name':'in.slurm','created_at':time.time(),'cluster_job_id':None,
                'reference_script':reference_script,'original_input_hashes':original,'input_hashes':inputs,'path_changes':changes,
                'provenance':provenance(source,cfg),'runtime':cfg,'disk_admission':disk}
        atomic_json(staging/'job.json',record)
        from openmc_import import publish_directory
        publish_directory(staging,root/identifier)
    except BaseException:
        if staging.exists():shutil.rmtree(staging)
        raise
    return {**_summary(record),'reference_script':reference_script,'batch_script':script,'input_hashes':inputs,'path_changes':changes,
            'directory':str(root/identifier),'resource_summary':r,'execution_summary':f"{name or 'openmc'}：{r['nodes']}节点，{r['ntasks']}个计算进程，每进程{r['cpus_per_task']}线程，队列{r.get('partition','站点默认')}，时限{r['time']}；使用内置Slurm模板与当前站点配置。",'network_used':False,'professional_acceptance':False,
            'next':'检查批处理脚本、资源和输入哈希；确认集群配置后调用openmc_slurm_submit。prepare未连接或提交集群。'}


def submit_slurm(submission_id: str) -> dict:
    with locked(submission_id) as (d,r):
        if r.get('cluster_job_id'):return {**_summary(r),'next':'已提交，不重复；调用openmc_slurm_status'}
        if r['state'] not in ('prepared','staging','staged'):raise Error('提交结果未知或已取消；先openmc_slurm_status核对原记录，禁止盲重交')
        p=_transfer_profile(r)
        current={str(x.relative_to(d/'input')):jobs.file_hash(x) for x in (d/'input').rglob('*') if x.is_file() and not x.is_symlink()}
        if current!=r['input_hashes'] or any(x.is_symlink() for x in (d/'input').rglob('*')):raise Error('准备后的输入已变化，未提交；保留本记录并重新准备正确模型')
        copied_bytes=sum(x.stat().st_size for x in (d/'input').rglob('*') if x.is_file())
        if shutil.disk_usage(workspace()).free < copied_bytes + r['runtime'].get('min_free_disk_bytes',1024**3) + 1024**2:
            raise Error('上传打包空间不足；保留准备记录，扩容或清理后重试同一submission_id')
        r['state']='staging';_persist(d,r)
        with tempfile.TemporaryDirectory(prefix='slurm-upload-',dir=d) as temporary:
            archive=Path(temporary)/'input.tar'
            with tarfile.open(archive,'w') as tar:
                for name in r['input_hashes']:tar.add(d/'input'/name,arcname=name,recursive=False)
            try:_remote(p,'stage',{'directory':r['remote_dir'],'inputs':r['input_hashes']},input_path=archive)
            except Error as exc:r['last_error']=str(exc);_persist(d,r);raise
        r.update(state='submitting',submitted_at=time.time(),last_error=None);_persist(d,r)
        try:
            out=_command(p,r['remote_dir'],'sbatch',['--parsable',r.get('batch_script_name','forge-job.sh')]).strip()
            match=re.fullmatch(r'(\d+)(?:;[A-Za-z0-9_.-]+)?',out)
            if not match:raise Error('sbatch未返回可识别编号；先按原submission_id查询，不重交')
            r.update(cluster_job_id=match.group(1),state='queued');_persist(d,r)
        except Error as exc:r.update(state='submission_unknown',last_error=str(exc));_persist(d,r);raise
        return {**_summary(r),'next':'提交不代表计算完成；使用openmc_slurm_status，完成后collect取回。'}


def _query(r):
    p=_transfer_profile(r);jid=r.get('cluster_job_id')
    native=r.get('batch_script_name')=='in.slurm'
    tag='forge-'+r['submission_id']
    job_name=(r.get('name') or 'openmc') if native else tag
    args=['--noheader','--format=%i|%T'+('|%k' if native and not jid else '')] + (['--jobs='+jid] if jid else ['--name='+job_name])
    try:queue=_command(p,r['remote_dir'],'squeue',args);queue_error=None
    except Error as exc:queue='';queue_error=str(exc)
    queued=[line.strip().split('|') for line in queue.splitlines() if line.strip()]
    if native and not jid:queued=[row for row in queued if len(row)>2 and row[2]==tag]
    query=['--noheader','--parsable2','--format=JobIDRaw,State,ExitCode'+(',Comment' if native and not jid else ''), '--starttime='+(datetime.fromtimestamp(r.get('submitted_at',r['created_at']),timezone.utc)-timedelta(days=1)).strftime('%Y-%m-%d')]
    query += ['--jobs='+jid] if jid else ['--name='+job_name]
    # Query terminal accounting even if queue has not disappeared yet.
    try:
        accounting=_command(p,r['remote_dir'],'sacct',query)
        rows=[line.strip().split('|') for line in accounting.splitlines() if line.strip()]
        if native and not jid:rows=[row for row in rows if len(row)>3 and row[3]==tag]
        accounting_error=None
    except Error as exc:rows=[];accounting_error=str(exc)
    ids={row[0] for row in queued+rows if len(row)>=2 and re.fullmatch(r'\d+',row[0]) and (jid is None or row[0]==jid)}
    if jid is None:
        if len(ids)!=1:
            r.update(state='submission_unknown',last_error='按原提交标记未找到唯一集群编号；联系运维核对，不能据此认定未提交或重交');return
        jid=ids.pop();r['cluster_job_id']=jid
    if native and jid:
        try:
            control=_command(p,r['remote_dir'],'scontrol',['show','job','--oneliner',jid])
            fields=dict(re.findall(r'(?:^|\s)([A-Za-z]+)=([^\s]+)',control))
            if fields.get('JobId')==jid and fields.get('JobState'):
                queued=[[jid,fields['JobState']]]
                r['scheduler_reason']=fields.get('Reason')
        except Error:
            pass  # Existing queue/accounting queries remain valid on older sites.
    row=next((x for x in rows if len(x)>=3 and x[0]==jid),None)
    q=next((x for x in queued if len(x)>=2 and x[0]==jid),None)
    accounting_state=row[1].split()[0].rstrip('+').upper() if row else ''
    terminal_accounting=accounting_state in {'COMPLETED','CANCELLED','FAILED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','BOOT_FAIL','DEADLINE','PREEMPTED'}
    state=(row[1] if terminal_accounting else q[1] if q else row[1] if row else '').split()[0].rstrip('+').upper() if row or q else ''
    code=row[2] if row else None
    r.update(scheduler_state=state or None,scheduler_exit_code=code,last_error=accounting_error or queue_error)
    if state=='COMPLETED' and code=='0:0':r['state']='completed'
    elif state.startswith('CANCELLED'):r['state']='cancelled'
    elif state in ('FAILED','TIMEOUT','NODE_FAIL','OUT_OF_MEMORY','BOOT_FAIL','DEADLINE','PREEMPTED') or state=='COMPLETED' and code not in (None,'0:0'):r['state']='failed'
    elif state in ('RUNNING','COMPLETING','SUSPENDED','RESIZING'):r['state']='running'
    elif state in ('PENDING','CONFIGURING','REQUEUED','REQUEUE_HOLD','REQUEUE_FED','SPECIAL_EXIT'):r['state']='queued'
    else:r.update(state='unknown',last_error=accounting_error or queue_error or '队列/会计记录尚无可确认终态；稍后重查，不自动重交')


def status_slurm(submission_id: str) -> dict:
    with locked(submission_id) as (d,r):
        if r['state'] not in TERMINAL|{'prepared','staging','staged'}:
            try:_query(r)
            except Error as exc:r['last_error']=str(exc);_persist(d,r);raise
            _persist(d,r)
        return {**_summary(r),'input_hashes':r['input_hashes'],'collected':bool(r.get('collected_job_id')),
                'next':'completed后调用openmc_slurm_collect，再用collected_job_id读取/作图/报告；未知时重查原记录。',
                'professional_acceptance':False}


def cancel_slurm(submission_id: str) -> dict:
    with locked(submission_id) as (d,r):
        if r['state'] in TERMINAL:return {**_summary(r),'changed':False}
        if r['state'] in ('prepared','staging','staged'):
            r['state']='cancelled';_persist(d,r)
            return {**_summary(r),'changed':True,'note':'尚未调用sbatch，取消本地准备；输入保留。'}
        if not r.get('cluster_job_id'):_query(r);_persist(d,r)
        if not r.get('cluster_job_id'):raise Error('集群编号未确认，不能猜测取消；联系运维核对原任务名')
        _command(_transfer_profile(r),r['remote_dir'],'scancel',[r['cluster_job_id']])
        r['cancel_requested']=True;_persist(d,r)
        return {**_summary(r),'next':'取消请求已发出；调用openmc_slurm_status确认实际终态。'}


def _verify_snapshot(path, expected):
    actual={}
    for item in path.rglob('*'):
        if item.is_symlink() or not (item.is_file() or item.is_dir()):raise Error('本地准备快照含符号链接或特殊文件，未发布结果')
        if item.is_file():actual[str(item.relative_to(path))]=jobs.file_hash(item)
    if actual!=expected:raise Error('本地准备快照与登记输入哈希不一致，未发布结果；保留远端原结果，不要改内部记录')


def _transfer_profile(record):
    # Endpoint/commands/material paths belong to this submission. Operational
    # transfer ceilings can change without moving a job to another cluster.
    saved=record['profile'];effective=dict(saved)
    if os.environ.get('MC_SLURM_CONFIG'):
        current=profile()
        identity=set(saved)|set(current)
        limits={'max_transfer_bytes','transfer_timeout_seconds','command_timeout_seconds'}
        # Older records predate optional scontrol. Its implicit default is not
        # an endpoint change; explicitly changed command prefixes still are.
        saved_identity={**saved,'commands':{'scontrol':['scontrol'],**saved.get('commands',{})}}
        if any(saved_identity.get(k)!=current.get(k) for k in identity-limits):
            return effective  # A different active profile never reroutes an existing task.
        for key in limits:effective[key]=current[key]
    return effective


def collect_slurm(submission_id: str) -> dict:
    with locked(submission_id) as (d,r):
        if r.get('collected_job_id'):return {'submission_id':submission_id,'job_id':r['collected_job_id'],'already_collected':True}
        if not r.get('submitted_at') or r['state'] in ('prepared','staging','staged'):
            raise Error('尚未提交Slurm，不能collect；先检查准备记录再submit。原状态未改变')
        _verify_snapshot(d/'input',r['input_hashes'])
        p=_transfer_profile(r)
        _query(r);_persist(d,r)
        if r['state'] not in TERMINAL:raise Error('集群终态未确认，不能收集为已完成结果；先查询状态')
        receipt=_remote(p,'inventory',{'directory':r['remote_dir'],'allow_failed':r['state']!='completed',
                        'inputs':r['input_hashes'],'shell_receipt':r.get('batch_script_name')=='in.slurm','exit_code':int((r.get('scheduler_exit_code') or '1:0').split(':')[0])})
        if receipt['input_hashes']!=r['input_hashes']:raise Error('远端计算输入与准备快照不一致')
        if r['state']=='completed' and receipt['exit_code']!=0:raise Error('Slurm成功但求解器退出码非零；保留冲突证据，不发布成功结果')
        outputs=receipt['outputs']
        for name,info in outputs.items():
            if not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or name in r['input_hashes'] or not re.fullmatch('[a-f0-9]{64}',info.get('sha256','')) or type(info.get('bytes')) is not int or info['bytes']<0:
                raise Error('远端结果清单不合法')
        size=sum(info['bytes'] for info in outputs.values())
        if size>p['max_transfer_bytes']:raise Error('结果超过max_transfer_bytes；调整与该任务连接身份一致的MC_SLURM_CONFIG限额后重试collect，不重新计算')
        required=2*size+sum(x.stat().st_size for x in (d/'input').rglob('*') if x.is_file())+r['runtime'].get('min_free_disk_bytes',1024**3)
        if shutil.disk_usage(workspace()).free<required:raise Error('结果取回空间不足；保留远端数据，清理或扩容后重试collect')
        root=path_in_workspace('.mc-jobs',exists=False,internal=True);root.mkdir(exist_ok=True)
        target=root/submission_id
        if target.exists():
            old=jobs.read(target)
            if old.get('slurm_submission_id')==submission_id:
                r['collected_job_id']=submission_id;_persist(d,r)
                return {'submission_id':submission_id,'job_id':submission_id,'already_collected':True}
            raise Error('同编号本地作业已存在，未覆盖')
        stage=Path(tempfile.mkdtemp(prefix='.slurm-collect-',dir=root))
        try:
            work=stage/'work';shutil.copytree(d/'input',work)
            _verify_snapshot(work,r['input_hashes'])
            archive=stage/'outputs.tar'
            _remote(p,'fetch',{'directory':r['remote_dir'],'names':list(outputs)},output_path=archive)
            seen=set()
            with tarfile.open(archive) as tar:
                for entry in tar:
                    if entry.name not in outputs or entry.name in seen or not entry.isfile() or entry.size!=outputs[entry.name]['bytes']:raise Error('取回文件与清单不一致')
                    path=work/entry.name;path.parent.mkdir(parents=True,exist_ok=True)
                    with tar.extractfile(entry) as src,path.open('wb') as dst:shutil.copyfileobj(src,dst)
                    if jobs.file_hash(path)!=outputs[entry.name]['sha256']:raise Error('结果传输SHA256不一致，不发布')
                    seen.add(entry.name)
            if seen!=set(outputs):raise Error('结果取回不完整')
            archive.unlink()
            if (work/'solver.log').exists():shutil.copyfile(work/'solver.log',stage/'solver.log')
            record={'job_id':submission_id,'slurm_submission_id':submission_id,'backend':'slurm','cluster_job_id':r['cluster_job_id'],
                    'state':r['state'],'name':r['name'],'case_dir':r['case_dir'],'work_dir':relative(target/'work'),
                    'mode':'run','threads':r['resources']['cpus_per_task'],'input_files':r['input_hashes'],
                    'output_files':{name:info['sha256'] for name,info in outputs.items()},'runtime':r['runtime'],
                    'provenance':{**r['provenance'],'execution_kind':'slurm_remote','transport':r['profile']['transport'],'remote_runtime':receipt.get('runtime'),
                                  'remote_nuclear_data_verified':False},
                    'submitted_at':r['submitted_at'],'finished_at':receipt['finished_at'],'exit_code':receipt['exit_code'],
                    'log_truncated':receipt['log_truncated'],'remote_dir':r['remote_dir'],
                    'remote_runtime':receipt.get('runtime', {'status':'not_recorded_solver_did_not_start'}),
                    'receipt_source':receipt.get('receipt_source','solver_sealed'),
                    'original_input_hashes':r['original_input_hashes'],'path_changes':r['path_changes']}
            atomic_json(stage/'job.json',record)
            from openmc_import import publish_directory
            publish_directory(stage,target)
        finally:
            if stage.exists():shutil.rmtree(stage)
        r['collected_job_id']=submission_id;_persist(d,r)
        return {'submission_id':submission_id,'job_id':submission_id,'state':r['state'],'directory':str(target),
                'output_count':len(outputs),'input_hashes':r['input_hashes'],'professional_acceptance':False,
                'next':'completed且有StatePoint时openmc_read_results(job_id)；图和报告沿用同一job_id。'}


def _schema(properties, required):return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
ID={'type':'string','pattern':'^[a-f0-9]{32}$','description':'openmc_slurm_prepare返回的submission_id；不是数字cluster_job_id。collect后返回可供普通结果工具使用的job_id。'}
RESOURCES=_schema({
 'nodes':{'type':'integer','minimum':1,'description':'申请的节点数；默认取站点resource_defaults，未配置时1。'},
 'ntasks':{'type':'integer','minimum':1,'description':'计算进程总数；默认取站点设置，未配置时1。大于1需确认MPI版OpenMC，并配置mpi_command；不是每进程线程数。'},
 'cpus_per_task':{'type':'integer','minimum':1,'description':'每个计算进程的CPU/线程数，同时设置OMP_NUM_THREADS和OpenMC -s；与ntasks分开，默认取站点配置，未配置时1。'},
 'time':{'type':'string','description':'集群运行时限，支持Slurm分钟数、HH:MM:SS等格式；默认取站点配置，未配置时00:10:00。实际可用上限由队列决定。'},
 'memory_mb':{'type':'integer','minimum':1,'description':'可选内存申请MiB，省略使用集群默认值。'},
 **{k:{'type':'string','description':v} for k,v in [('partition','集群分区名称，由运维提供。'),('account','集群计费/项目account，非登录用户名。'),('qos','可选集群QOS，由运维提供。')]}
},[])
RESOURCES['description']='资源请求；优先复用已确认的站点resource_defaults，本次字段覆盖同名默认值。无站点资源默认时用1节点/1进程/1线程/10分钟，队列使用站点默认；没有现场脚本也可用内置模板，但集群入口、环境和本次资源仍须明确。'
SLURM_TOOLS=[
 {'name':'openmc_slurm_prepare','description':'离线校核并快照已有模型，使用内置默认模板和站点设置生成普通in.slurm，返回资源摘要与输入哈希，绝不连接或提交集群。现场脚本可选参考，不是使用前提；先明确MC_SLURM_CONFIG中的集群入口与环境。此能力不代表真实HPC已验证。',
  'inputSchema':_schema({'case_dir':{'type':'string','minLength':1,'description':'已有模型在MC_WORKSPACE内的相对目录，先生成/导入、预览并按需要校核。'},'resources':RESOURCES,
  'name':{'type':'string','minLength':1,'maxLength':120,'description':'Slurm可见任务名，省略为openmc；同名任务由内部提交记录和job ID区分，不用名称猜取消目标。'}},['case_dir']), 'function':prepare_slurm},
 *[{'name':'openmc_slurm_'+name,'description':description,'inputSchema':_schema({'submission_id':ID},['submission_id']),'function':function}
 for name,description,function in [
 ('submit','上传已准备快照并调用sbatch；同一编号不重复提交。回执丢失标未知，先status按唯一任务名核对。会实际访问管理员配置的登录节点。',submit_slurm),
 ('status','查询已有Slurm准备/作业，squeue与sacct分开确认排队/运行/终态；队列消失不视为完成。结果仍在远端，completed后需collect。',status_slurm),
 ('cancel','取消尚未提交的准备或请求scancel；不删除输入/产物，实际取消终态需status确认。',cancel_slurm),
 ('collect','将确认终态的远端原始结果按输入/输出SHA256取回本工具服务端，原子发布普通job_id供结果/图/报告工具使用；不提供客户端网络下载，不删除远端数据。',collect_slurm)]],
]
for tool in SLURM_TOOLS:
    tool['outputSchema']={'type':'object'}
    tool['annotations']={'readOnlyHint':tool['name']=='openmc_slurm_status','destructiveHint':tool['name']=='openmc_slurm_cancel',
                         'idempotentHint':tool['name']!='openmc_slurm_prepare','openWorldHint':tool['name']!='openmc_slurm_prepare'}
