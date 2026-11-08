"""Registered public examples have explicit data and execution status, not implied acceptance."""
import json

from business import get_case
from openmc_runtime import Error, path_in_workspace, provenance
from openmc_tools import create_model


def prepare_example(case_id: str, case_dir: str, particles: int = 200, batches: int = 10, inactive: int = 2):
    definition = get_case(case_id)
    if not definition['case'].get('template'):
        raise Error('此例题尚缺材料，不能生成或运行：' + case_id)
    case = definition['case']
    value = create_model(case_dir, case['template'], particles, batches, inactive)
    directory = path_in_workspace(case_dir)
    if case.get('fault') == 'run_mode_typo':
        path = directory / 'settings.xml'; text = path.read_text()
        original = '<run_mode>eigenvalue</run_mode>'
        if text.count(original) != 1:raise Error('故障练习模板不匹配，没有擅自修改')
        path.write_text(text.replace(original, '<run_mode>eigenvale</run_mode>'))
        marker = json.loads((directory / 'mc-case.json').read_text())
        marker.update(intentional_fault='run_mode_typo', expected_first_run='failed')
        (directory / 'mc-case.json').write_text(json.dumps(marker, ensure_ascii=False, indent=2)+'\n')
    return {**value, 'warnings': list(dict.fromkeys([*value.get('warnings', []), *case.get('warnings', [])])), 'case_id': case_id, 'case_dir': case_dir, 'execution_status': 'not_run',
            'expected_first_run': case.get('expected_first_run', 'completed'), 'provenance': provenance(directory),
            'next': '使用openmc_submit_job提交；生成成功不等于计算已经完成。'}
