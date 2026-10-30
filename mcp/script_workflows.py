"""Run registered multi-stage tutorials intact, retaining their scientific products."""
from __future__ import annotations
import json
from pathlib import Path
import runpy
import shutil
from openmc_runtime import atomic_json


def postprocess(item: dict) -> dict:
    import matplotlib.pyplot as plt
    import openmc
    import xml.etree.ElementTree as ET
    settings=ET.parse('model.xml' if Path('model.xml').is_file() else 'settings.xml').getroot()
    batch=settings.findtext('.//batches')
    actual=f'statepoint.{batch}.h5'
    if not Path(actual).is_file():raise ValueError('缺少本次最终批次结果：'+actual)
    original=openmc.StatePoint;reads=[]
    def statepoint(filename,*args,**kwargs):
        reads.append({'script_requested':str(filename),'actual_final_batch':actual})
        return original(actual,*args,**kwargs)
    openmc.StatePoint=statepoint
    figures=[]
    def show(*args,**kwargs):
        for number in plt.get_fignums():
            name=f'postprocess-figure-{len(figures)+1}.png'
            plt.figure(number).savefig(name,dpi=140,bbox_inches='tight');figures.append(name)
        plt.close('all')
    plt.show=show
    script='plot_spectrum.py' if item['id']=='source-pincell' else 'show_flux.py'
    runpy.run_path(script,run_name='__main__');show()
    value={'example_id':item['id'],'stage':'postprocess','source_file':script,
           'statepoint_selection':reads,'figures':figures,'professional_acceptance':False,
           'note':'沿用官方后处理，明确读取本次最终批次；保留显式运行设置覆盖。'}
    atomic_json(Path('script-result-postprocess.json'),value)
    return value


def execute_script(item: dict, step: str, parameters: dict) -> dict:
    from notebook_runtime import install_runtime_checks
    import matplotlib.pyplot as plt
    import openmc
    import numpy as np
    install_runtime_checks(item['id'], settings_overrides=parameters)
    figures = []
    def save_figures(*args, **kwargs):
        for number in plt.get_fignums():
            name = f'{step}-figure-{len(figures)+1}.png'
            plt.figure(number).savefig(name, dpi=140, bbox_inches='tight')
            figures.append(name)
        plt.close('all')
    plt.show = save_figures
    if item['workflow'] == 'depletion_restart' and step == 'execute':
        filename = 'run_depletion.py'
    elif step == 'restart':
        # The upstream script passes a fuel Cell to two result APIs that accept
        # only Material/id. Resolve exactly that cell's existing fill; no values
        # or physical inputs are substituted.
        for method in ('get_atoms','get_reaction_rate'):
            original=getattr(openmc.deplete.Results,method)
            def material_handle(self,mat,*args,_original=original,**kwargs):
                if isinstance(mat,openmc.Cell) and isinstance(mat.fill,openmc.Material):mat=mat.fill
                return _original(self,mat,*args,**kwargs)
            setattr(openmc.deplete.Results,method,material_handle)
        filename = 'restart_depletion.py'
        # The restart overwrites the same depletion/statepoint names upstream.
        # A prior step snapshot is retained and stage products have explicit roles.
        import xml.etree.ElementTree as ET
        settings=ET.parse('settings.xml').getroot()
        previous_point='statepoint.'+settings.findtext('batches')+'.h5'
        original_statepoint_init=openmc.StatePoint.__init__
        def statepoint_init(self,filename,*args,**kwargs):
            if str(filename)=='statepoint.100.h5':filename=previous_point
            return original_statepoint_init(self,filename,*args,**kwargs)
        openmc.StatePoint.__init__=statepoint_init
        previous = Path('before-restart');previous.mkdir()
        for name in ('depletion_results.h5', previous_point, 'summary.h5'):
            if not Path(name).is_file():raise ValueError('燃耗重启缺前置文件：'+name)
            shutil.copy2(name, previous/name)
    else:
        filename = Path(item['source_file']).name
    values = runpy.run_path(filename, run_name='__main__')
    save_figures()
    summary = {'example_id':item['id'], 'stage':step, 'source_file':filename,
               'source_revision':item['source_revision'], 'overrides':parameters,
               'figures':figures, 'professional_acceptance':False,
               'effective_settings_record':'script-settings.json' if Path('script-settings.json').exists() else None,
               'restart_statepoint':previous_point if step=='restart' else None}
    if item['workflow'] in ('depletion', 'depletion_restart'):
        time = np.asarray(values['time']);keff = np.asarray(values['keff'])
        data = {'time_days':time.tolist(), 'keff_mean':keff[:,0].tolist(),
                'keff_std_dev':keff[:,1].tolist(),
                'U235_atoms':np.asarray(values['n_U235']).tolist(),
                'Xe135_capture_rate':np.asarray(values['Xe_capture']).tolist()}
        if not all(np.all(np.isfinite(v)) for v in data.values()):
            raise ValueError('燃耗后处理出现非有限值；未将其报告为有效结果')
        summary.update(kind='depletion', series=data, result='depletion_results.h5')
    else:
        edges = np.asarray(values['t_grid']);mean=np.asarray(values['density_mean'])
        if len(edges)!=len(mean)+1 or not np.all(np.isfinite(mean)):
            raise ValueError('脉冲结果与原时间分箱不一致')
        summary.update(kind='time_density',series={'time_edges_seconds':edges.tolist(),
                       'bin_averaged_density':mean.tolist()})
    atomic_json(Path('script-result-'+step+'.json'),summary)
    return summary
