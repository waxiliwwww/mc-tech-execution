"""Readable execution facts and optional consistency binding, not human consent."""
from __future__ import annotations
import hashlib,json,math
from pathlib import Path
import xml.etree.ElementTree as ET
from openmc_runtime import Error,relative


def checked_run(mode, threads):
    if mode!='run' or type(threads) is not int or not 1<=threads<=64:
        raise Error('执行说明当前支持mode=run、threads=1..64；其他模式保留原提交方式')


def preview_id(context, directory, case_dir, mode, threads):
    """Normalize only relative, copied nuclear resources, not external references."""
    copied=set()
    model=directory/('model.xml' if (directory/'model.xml').is_file() else 'materials.xml')
    reference=ET.parse(model).getroot().findtext('.//cross_sections')
    if reference and not Path(reference).is_absolute():
        index=(directory/reference).resolve()
        if index.is_relative_to(directory.resolve()):
            copied.add(str(index))
            if index.suffix.lower()=='.xml' and index.is_file():
                root=ET.parse(index).getroot();base=Path(root.findtext('directory') or '.')
                if not base.is_absolute():
                    for node in root.findall('library'):
                        name=Path(node.get('path') or '')
                        if not name.is_absolute():
                            path=(index.parent/base/name).resolve()
                            if path.is_relative_to(directory.resolve()):copied.add(str(path))
    def normalize(value):
        if isinstance(value,list):return [normalize(v) for v in value]
        if isinstance(value,dict):
            if value.get('path') in copied:
                key=Path(value['path']).resolve().relative_to(directory.resolve()).as_posix()
                if key in context['input_hashes']:
                    return {'local_input':key,'sha256':context['input_hashes'][key]}
            return {k:normalize(v) for k,v in value.items()}
        return value
    data={'case_dir':case_dir,'mode':mode,'threads':threads,
          'context':{**context,'nuclear_data':normalize(context.get('nuclear_data'))}}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def data_summary(context):
    n=context.get('nuclear_data') or {}
    return {k:n.get(k) for k in ('energy_mode','library','index','metadata_complete','content_verified')} | {
        'library_files':len(n.get('libraries',[])), 'identity_note':context.get('identity_note')}


def describe(directory, cfg, input_hashes, mode, threads):
    from openmc_runtime import api
    from openmc_model_context import model_context
    from openmc_tools import validate_inputs
    checked_run(mode,threads)
    validation=validate_inputs(relative(directory),api_check=False,semantic_check=True)
    facts=api('execution-summary',directory,{},cfg)
    context=model_context(directory,cfg)
    if context['input_hashes']!=input_hashes:
        raise Error('输入在生成执行说明期间改变；未提交，请重新预览')
    marker=directory/'mc-case.json'
    history=json.loads(marker.read_text()).get('parameter_changes',[]) if marker.is_file() else []
    current={p['name']:p for p in facts['parameters']}
    changes=[]
    for entry in history:
        parameter=current.get(entry['name']);actual=parameter.get('current') if parameter else None
        expected=entry['after']
        matches=(math.isclose(actual,expected,rel_tol=1e-12,abs_tol=1e-15)
                 if type(actual) in (int,float) and type(expected) in (int,float) else actual==expected and parameter is not None)
        changes.append({**entry,'current':actual,'matches_recorded_change':matches,
                        'object':(parameter or {}).get('object')})
    warnings=list(facts.get('warnings',[]))
    semantic=validation.get('semantic') or {}
    warnings += [p.get('detail') or str(p) for p in semantic.get('problems',[]) if p.get('severity')=='warning']
    if any(not c['matches_recorded_change'] for c in changes):warnings.append('改参记录与当前参数不一致；以current为准，未把旧改参记录当作仍已生效')
    if not context['dependencies_ready']:warnings.append('依赖尚未齐备，请先处理依赖检查结果')
    settings=facts['settings']
    change_lines=[f"{c['name']}：记录前值{c['before']} → 当前{c['current']} {c.get('unit') or ''}" for c in changes]
    explanation=[f"准备计算：{relative(directory)}；{threads}线程；最长{cfg['job_timeout_seconds']}秒。"]
    explanation+=change_lines or ['没有可核实的派生改参记录；当前参数以模型读回值为准。']
    explanation.append(f"当前设置：{settings['run_mode']}；粒子数{settings['particles']}，批次{settings['batches']}，非活跃批{settings['inactive']}，种子{settings['seed']}。")
    index=(context['nuclear_data'].get('index') or {}).get('path')
    explanation.append('当前计分：'+('；'.join(str(t['id'])+':'+','.join(t['scores']) for t in facts.get('tallies',[])) or '没有计分定义'))
    explanation.append('核数据：'+str(index or '未定位；须先确认'))
    explanation.append('尚未提交计算；此说明编号核对执行条件，不证明用户已同意或物理正确。')
    ready=bool(validation['structure_valid'] and semantic.get('ok') and context['dependencies_ready'])
    return {'preview_id':preview_id(context,directory,relative(directory),mode,threads),
            'case_dir':relative(directory),'operation':{'mode':mode,'threads':threads,
                'timeout_seconds':cfg['job_timeout_seconds'],'max_log_bytes':cfg['max_log_bytes']},
            'settings':settings,'parameter_changes':changes,'tallies':facts.get('tallies',[]),
            'tallies_truncated':facts.get('tallies_truncated',False),'nuclear_data':data_summary(context),
            'validation':{'structure_valid':validation['structure_valid'],'api_parsed':True,
                'semantic_checked':semantic.get('checked',False),'semantic_ok':semantic.get('ok',False),
                'semantic_complete':semantic.get('complete',False),'errors':[p for p in semantic.get('problems',[]) if p.get('severity')=='error'][:20]},
            'ready_for_submission_checks':ready,'warnings':list(dict.fromkeys(warnings))[:20],
            'warnings_truncated':len(set(warnings))>20,'human_summary':'\n'.join(explanation),
            'scope':'单次run执行说明；只说明已读取条件，未检查几何重叠/专业正确性，不保证宿主展示或已取得用户确认。'}


def receipt(context, case_dir, mode, threads, expected=None):
    result={'preview_bound':expected is not None,'mode':mode,'threads':threads,'case_dir':case_dir,
            'input_files':len(context.get('input_hashes',{})), 'nuclear_data':data_summary(context),
            'note':'以提交快照为准；不证明用户已阅读或同意，不代替专业验收。'}
    if expected:
        result.update(preview_id=expected['preview_id'],binding_verified=True,
                      settings=expected['settings'],parameter_changes=expected['parameter_changes'],
                      tallies=expected['tallies'],tallies_truncated=expected['tallies_truncated'],
                      warnings=expected['warnings'])
    else:result.update(binding_verified=False,note='未提供preview_id，未核对先前预览；实际输入见input_hashes，不能声称已按用户看过的版本执行。')
    return result
