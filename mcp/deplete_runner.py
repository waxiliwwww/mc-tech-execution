"""燃耗运行器：在作业工作目录内按 mc-case.json 的 depletion 配置执行 openmc.deplete。"""
import json
import os
from pathlib import Path

import openmc


def main() -> None:
    marker = Path('mc-case.json')
    if not marker.is_file():
        raise SystemExit('缺少mc-case.json，无法确认燃耗时配置')
    spec = json.loads(marker.read_text(encoding='utf-8')).get('depletion')
    if not spec:
        raise SystemExit('算例未配置燃耗；先调用openmc_configure_depletion写入功率与时间步')
    chain = spec.get('chain_file') or os.environ.get('OPENMC_CHAIN_FILE')
    if not chain or not Path(chain).is_file():
        raise SystemExit('找不到燃耗链文件：' + str(chain))
    cross_sections = os.environ.get('OPENMC_CROSS_SECTIONS')
    if cross_sections and Path(cross_sections).is_file():
        openmc.config['cross_sections'] = cross_sections
    model = openmc.Model.from_model_xml('model.xml') if Path('model.xml').exists() else openmc.Model.from_xml()
    from openmc.deplete import CoupledOperator, PredictorIntegrator
    operator = CoupledOperator(model, chain)
    integrator = PredictorIntegrator(operator, spec['timesteps'], power=spec['power_watts'],
                                     timestep_units=spec.get('timestep_unit', 'd'))
    integrator.integrate()
    if not Path('depletion_results.h5').is_file():
        raise SystemExit('燃耗结束但未见depletion_results.h5')
    print('depletion_results.h5 已生成')


if __name__ == '__main__':
    main()
