"""Public response projections. Persisted evidence and internal consumers stay full."""
from copy import deepcopy

def project(value, detail, kind):
    if detail not in ('summary', 'full'):
        raise ValueError('detail须为summary或full')
    result = deepcopy(value)
    result['detail'] = detail
    if detail == 'full': return result
    omitted = []
    if kind == 'capabilities':
        schema = result.pop('spec_schema', {})
        result['spec_required'] = schema.get('required', [])
        result['spec_fields'] = list(schema.get('properties', {}))
        omitted.append('spec_schema')
    elif kind == 'results':
        temp = (result.get('scientific_checks') or {}).get('temperature')
        if temp and 'materials' in temp:
            records = temp.pop('materials')
            temp['material_records_returned'] = len(records)
            omitted.append('scientific_checks.temperature.materials')
    elif kind == 'example':
        for step in result.get('steps', {}).values():
            for key in ('input_hashes', 'input_files', 'files', 'log_tail', 'input_integrity'):
                if key in step: step.pop(key); omitted.append('steps.'+key)
    elif kind == 'repair':
        plan = result.get('plan', {})
        context = plan.get('context', {})
        if context:
            plan['context'] = {k: context[k] for k in ('origin_job_id','case_dir','mode') if k in context}
            omitted.append('plan.context.input_files/diagnosis')
    if omitted:
        result['detail_omitted'] = sorted(set(omitted))
        result['detail_hint'] = '完整元数据用同一查询detail=full；修复计划可用read_repair_plan(plan_id)读取，勿重复创建修复副本。'
    return result
