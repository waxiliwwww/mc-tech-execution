"""Compose existing derivation/tally/readback/bound submission, without physics guesses.

Always publishes a new directory. Repeating a lost reply cannot submit again:
the target already exists and the caller must find the original job instead.
"""
import copy
from decimal import Decimal, InvalidOperation
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

from openmc_runtime import Error, api, path_in_workspace, relative, file_location


def _semantic_xml(text):
    n=ET.fromstring(text)
    # SDK rebuilds may serialize -5 as -5.0. Keep filter order, IDs and
    # every boundary while comparing numeric values rather than typography.
    values=[]
    for token in (n.text or '').split():
        try:values.append(Decimal(token))
        except InvalidOperation:values.append(token)
    return (n.tag,sorted(n.attrib.items()),values,[_semantic_xml(ET.tostring(c,encoding='unicode')) for c in n])


def _same_grid(a,b):
    return b is not None and _semantic_xml(a['definition'])==_semantic_xml(b['definition']) and \
        [_semantic_xml(x) for x in a['filter_definitions']]==[_semantic_xml(x) for x in b['filter_definitions']] and \
        sorted(_semantic_xml(x) for x in a['meshes'])==sorted(_semantic_xml(x) for x in b['meshes'])


def mesh_tallies(directory):
    path=directory/'model.xml'
    if not path.exists():path=directory/'tallies.xml'
    if not path.exists():return []
    if path.is_symlink():raise Error('计分输入为符号链接，未读取或修改')
    try:root=ET.parse(path).getroot()
    except ET.ParseError as exc:raise Error('计分XML无法解析：'+str(exc)) from exc
    filters={n.get('id'):n for n in root.iter('filter') if n.get('id')}
    result=[]
    for tally in root.iter('tally'):
        nodes=[filters.get(i) for i in (tally.findtext('filters') or '').split()]
        if any(n is not None and n.get('type')=='mesh' for n in nodes):
            definition=copy.deepcopy(tally)
            scores=(tally.findtext('scores') or '').split()
            for n in definition.findall('scores'):definition.remove(n)
            result.append({'id':int(tally.get('id')),'name':tally.get('name'),
                           'scores':scores,'definition':ET.tostring(definition,encoding='unicode'),
                           'filter_definitions':[ET.tostring(n,encoding='unicode') if n is not None else None for n in nodes],
                           'meshes':[ET.tostring(n,encoding='unicode') for n in root.iter('mesh')]})
    return result


def run_case(case_dir: str, target_dir: str, parameters: dict | None = None,
             mesh_scores: list | None = None, tally_id: int | None = None,
             submit: bool = False, threads: int = 1, name: str | None = None) -> dict:
    """Prepare-only by default; submit=True consumes the user's compute permission."""
    from openmc_scan import copy_inputs, apply_and_verify_parameters
    from openmc_import import publish_directory
    from openmc_tools import preview_input, update_tally, submit_job
    import openmc_scores
    import openmc_jobs as jobs
    if type(submit) is not bool:raise Error('submit须为布尔值；未获计算授权时使用false')
    if type(threads) is not int or not 1<=threads<=64:raise Error('threads须为1..64整数')
    jobs.checked_name(name)
    if parameters is not None and not isinstance(parameters,dict):raise Error('parameters须为已发现的参数对象')
    if mesh_scores is not None:
        if not isinstance(mesh_scores,list) or not mesh_scores or any(not isinstance(s,str) or not s.strip() for s in mesh_scores):
            raise Error('mesh_scores须为非空score列表，例如["flux","fission"]')
        unknown,_=openmc_scores.check(mesh_scores)
        if unknown:raise Error('未知score：'+','.join(unknown)+'；未修改或提交')
    if tally_id is not None and (type(tally_id) is not int or tally_id<=0):raise Error('tally_id须为正整数')
    if tally_id is not None and mesh_scores is None:raise Error('tally_id用于mesh_scores，须同时提供')
    source=path_in_workspace(case_dir);target=path_in_workspace(target_dir,exists=False)
    if target.exists():
        raise Error('目标目录已存在，未修改或重复提交；若上次回复丢失，先openmc_list_jobs按case_dir/任务名查原作业。确认仅准备且要追加条件时，从该副本派生另一个target_dir。')
    if source==target or target.is_relative_to(source) or source.is_relative_to(target):
        raise Error('target_dir须在原算例目录之外且不能是其父目录')
    selected=None
    if mesh_scores is not None:
        candidates=mesh_tallies(source)
        choices=[t for t in candidates if tally_id is None or t['id']==tally_id]
        if len(choices)!=1:
            return {'state':'needs_selection','submitted':False,'case_dir':case_dir,
                    'mesh_candidates':[{k:t[k] for k in ('id','name','scores')} for t in candidates],
                    'note':'没有唯一的目标网格计分，未修改或提交。向用户确认目标网格；无网格时需明确网格定义，再用原计分工具创建。不要改为全局计分。'}
        selected=choices[0]
    if parameters:api('check-parameters',source,{'combinations':[parameters]})
    target.parent.mkdir(parents=True,exist_ok=True)
    holder=Path(tempfile.mkdtemp(prefix='agent-run-stage-',dir=target.parent));stage=holder/'model'
    try:
        copy_inputs(source,stage)
        diff=apply_and_verify_parameters(stage,parameters)['parameter_diff'] if parameters else None
        check=None
        if selected:
            before=next(t for t in mesh_tallies(stage) if t['id']==selected['id'])
            if not _same_grid(selected,before) or not set(selected['scores'])<=set(before['scores']):
                raise Error('派生改参后原网格/计分读回不一致，未发布副本或提交')
            merged=list(dict.fromkeys(before['scores']+mesh_scores))
            if merged!=before['scores']:update_tally(relative(stage),before['id'],scores=merged)
            after=next((t for t in mesh_tallies(stage) if t['id']==before['id']),None)
            if not _same_grid(before,after) or not set(merged)<=set(after['scores']):
                raise Error('原网格/过滤/计分读回不一致，未发布副本或提交')
            check={'tally_id':after['id'],'scores_before':before['scores'],'scores_after':after['scores'],
                   'required_scores':mesh_scores,'same_mesh_and_filters':True,'verified':True}
        staged=preview_input(relative(stage))
        publish_directory(stage,target)
    finally:
        shutil.rmtree(holder)
    final=preview_input(target_dir,mode='run',threads=threads)
    if final['input_hashes']!=staged['input_hashes']:raise Error('发布后的输入发生变化，未提交；保留副本供检查')
    result={'state':'prepared','submitted':False,'source_case_dir':case_dir,'case_dir':target_dir,
            **file_location(target),'parameter_diff':diff,'mesh_check':check,
            'input_hashes':final['input_hashes'],'summary':final['summary'],
            'execution_preview':final['execution_preview'],'professional_acceptance':False,
            'note':'原模型和已提交作业保留；mesh_scores只给同一已有网格追加，不新建全局计分。执行说明不是用户同意凭证。'}
    if submit:
        job=submit_job(target_dir,threads=threads,name=name,preview_id=final['execution_preview']['preview_id'])
        result.update(state='submitted',submitted=True,job=job,job_id=job['job_id'])
    return result


RUN_TOOLS=[{'name':'openmc_run_case',
    'description':'用户要改参并计算、或向同一原网格追加flux/fission时优先使用：在新副本内改参、保留原网格/过滤/已有score并追加mesh_scores、读回核对、生成最终执行说明；submit=true才按该快照提交run，默认只准备不计算。唯一已有网格可自动定位，多个/没有则needs_selection且不写盘。原模型/旧作业不变。每次target_dir须为新目录，重复已有目标不再提交；回复未知先查询原作业。复杂新网格/非网格/燃耗/扫描继续用原工具。',
    'inputSchema':{'type':'object','properties':{
        'case_dir':{'type':'string','minLength':1,'description':'已有算例，相对服务器MC_WORKSPACE；原输入不改。'},'target_dir':{'type':'string','minLength':1,'description':'尚不存在的新副本目录，相对服务器MC_WORKSPACE，须在原算例之外；后续沿返回目录继续。'},
        'parameters':{'type':'object','additionalProperties':{'type':'number'},'description':'可选，键取list_parameters，保留原单位；不猜材料。'},
        'mesh_scores':{'type':'array','minItems':1,'items':{'type':'string','minLength':1},'description':'给同一已有网格追加这些score，保留原score/过滤；不是全局计分。'},
        'tally_id':{'type':'integer','minimum':1,'description':'多个网格时须按用户选择指定；只有一个网格可省略，不能指向全局计分。'},
        'submit':{'type':'boolean','default':False,'description':'用户已明确授权计算且条件确定时true，否则只准备。'},
        'threads':{'type':'integer','minimum':1,'maximum':64,'description':'本次run的线程数，默认1；不扩大用户资源授权。'},
        'name':{'type':'string','minLength':1,'maxLength':120,'description':'可选作业名，供丢回复后list_jobs查找；身份仍为job_id。'}},
        'required':['case_dir','target_dir'],'additionalProperties':False},
    'outputSchema':{'type':'object'},'function':run_case,
    'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':False}}]
