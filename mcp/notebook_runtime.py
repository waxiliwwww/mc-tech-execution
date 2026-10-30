"""Declared notebook adapter: prepare real data before native calculation entrypoints."""
from pathlib import Path
import inspect
import os
from openmc_runtime import config
from nuclear_data import ensure_for_model


def install_runtime_checks(example_id=None, settings_overrides=None):
    import openmc
    import urllib.request
    from official_assets import local_response
    def urlopen(address,*args,**kwargs):
        url=address if isinstance(address,str) else address.full_url
        # Tutorial URLs are frozen inputs, not runtime internet dependencies.
        return local_response(url)
    urllib.request.urlopen=urlopen
    import numpy as np
    if not hasattr(np, 'trapz'):
        np.trapz=np.trapezoid  # Same trapezoidal integral, renamed by NumPy.
    # OpenMC 0.16 retained Plot() as a compatibility function but removed the
    # classmethod used by several unchanged official notebooks. Delegate that
    # documented constructor to the corresponding real SlicePlot class.
    if not hasattr(openmc.Plot, 'from_geometry'):
        openmc.Plot.from_geometry = openmc.SlicePlot.from_geometry
    def sync_configuration():
        # Older official notebooks assign environment variables after importing
        # OpenMC; 0.16 reads them once at import, so sync the same explicit paths.
        for key, variable in [('cross_sections','OPENMC_CROSS_SECTIONS'),
                              ('mg_cross_sections','OPENMC_MG_CROSS_SECTIONS'),
                              ('chain_file','OPENMC_CHAIN_FILE')]:
            if os.environ.get(variable):openmc.config[key]=os.environ[variable]
    recorder=None
    if example_id and example_id.startswith('notebook-'):
        from notebook_stages import StageRecorder
        recorder=StageRecorder(example_id)
    def check(directory):
        sync_configuration()
        p=Path(directory or '.').resolve();adaptations=[]
        if example_id=='notebook-cad-based-geometry':
            from notebook_stages import cad_guard
            adaptations.append(cad_guard(p))
        data=None
        if (p/'model.xml').exists() or ((p/'materials.xml').exists() and (p/'settings.xml').exists()):
            cfg=config()
            if openmc.config.get('mg_cross_sections'):cfg['mg_cross_sections']=str(openmc.config['mg_cross_sections'])
            data=ensure_for_model(p,cfg)
        return {'nuclear_data':data,'adaptations':adaptations}
    def apply_model_settings(model):
        if not settings_overrides:return
        from openmc_runtime import atomic_json
        import json
        keys=('particles','batches','inactive','seed')
        if 'inactive' in settings_overrides and model.settings.run_mode!='eigenvalue':
            raise ValueError('固定源模式不接受inactive覆盖')
        original={key:getattr(model.settings,key) for key in keys}
        for key in keys:
            if key in settings_overrides:setattr(model.settings,key,settings_overrides[key])
        effective={key:getattr(model.settings,key) for key in keys}
        path=Path('script-settings.json');records=json.loads(path.read_text()) if path.exists() else {'applications':[]}
        records['applications'].append({'original':original,'effective':effective,'run_mode':model.settings.run_mode})
        atomic_json(path,records)
    original_model_run=openmc.Model.run
    def model_run(self,*args,**kwargs):
        apply_model_settings(self)
        return original_model_run(self,*args,**kwargs)
    openmc.Model.run=model_run
    original_run=openmc.run
    def run(*args,**kwargs):
        if example_id=='notebook-gamma-detector' and args and args[0]=='/home/cpf/Desktop/openmc/build/bin/openmc':
            print('运行适配：官方Notebook中开发者机器的路径改用当前已配置OpenMC程序；未改粒子数或物理设置。',flush=True)
            args=args[1:];kwargs.setdefault('openmc_exec',config()['openmc_command'][0])
        bound=inspect.signature(original_run).bind_partial(*args,**kwargs)
        directory=Path(bound.arguments.get('cwd','.')).resolve()
        stage=recorder.begin('transport',directory) if recorder else None
        try:
            preflight=check(directory)
            if recorder:recorder.effective(stage,directory,preflight)
            result=original_run(*args,**kwargs)
        except BaseException as error:
            if recorder:recorder.finish(stage,directory,'failed',error)
            raise
        if recorder:recorder.finish(stage,directory)
        return result
    openmc.run=run
    original_plot=openmc.Model.plot
    def plot(self,*args,**kwargs):
        sync_configuration()
        return original_plot(self,*args,**kwargs)
    openmc.Model.plot=plot
    # Direct C API examples bypass the executable and also need the same data gate.
    import openmc.lib
    original_init=openmc.lib.init
    def init(*args,**kwargs):
        directory=Path.cwd().resolve()
        stage=recorder.begin('library_initialization',directory) if recorder else None
        try:
            preflight=check(directory)
            if recorder:recorder.effective(stage,directory,preflight)
            result=original_init(*args,**kwargs)
        except BaseException as error:
            if recorder:recorder.finish(stage,directory,'failed',error)
            raise
        if recorder:recorder.finish(stage,directory,'initialized')
        return result
    openmc.lib.init=init
    if recorder:
        library_stage=None
        def begin_library(kind):
            nonlocal library_stage
            directory=Path.cwd().resolve()
            library_stage=recorder.begin(kind,directory)
            recorder.effective(library_stage,directory,{'note':'已初始化C API；内存材料/温度的前后状态另存properties文件。'})
            recorder.properties(library_stage,'before')
            return library_stage
        original_lib_run=openmc.lib.run
        def library_run(*args,**kwargs):
            stage=begin_library('library_transport');directory=Path.cwd().resolve()
            try:result=original_lib_run(*args,**kwargs)
            except BaseException as error:
                recorder.finish(stage,directory,'failed',error);raise
            recorder.properties(stage,'after')
            recorder.finish(stage,directory);return result
        openmc.lib.run=library_run
        original_next_batch=openmc.lib.next_batch
        def next_batch(*args,**kwargs):
            nonlocal library_stage
            import json
            source=json.loads((recorder.root/'notebook-progress.json').read_text())
            if library_stage is None or library_stage['kind']!='library_batch_sequence' or library_stage['original_cell_index']!=source['original_cell_index']:
                begin_library('library_batch_sequence')
                library_stage['batches_executed']=0
            try:result=original_next_batch(*args,**kwargs)
            except BaseException as error:
                recorder.finish(library_stage,Path.cwd(),'failed',error);raise
            library_stage['batches_executed']+=1
            recorder.properties(library_stage,'after')
            recorder.finish(library_stage,Path.cwd())
            return result
        openmc.lib.next_batch=next_batch
        original_statepoint_write=openmc.lib.statepoint_write
        def statepoint_write(filename=None,*args,**kwargs):
            result=original_statepoint_write(filename,*args,**kwargs)
            if library_stage is None:
                raise ValueError('C API已写结果但缺少可关联的求解阶段；保留文件，不报告有效阶段')
            if filename is None:
                # OpenMC's public API default is the actual current batch.
                filename='statepoint.'+str(openmc.lib.current_batch())+'.h5'
            recorder.capture_statepoint(library_stage,filename)
            return result
        openmc.lib.statepoint_write=statepoint_write
        original_statepoint_init=openmc.StatePoint.__init__
        def statepoint_init(self,filename,*args,**kwargs):
            recorder.record_read(filename)
            return original_statepoint_init(self,filename,*args,**kwargs)
        openmc.StatePoint.__init__=statepoint_init
    import openmc.deplete
    original_operator=openmc.deplete.CoupledOperator.__init__
    def operator(self,model,chain_file=None,*args,**kwargs):
        # Coupled depletion introduces chain nuclides after the first solve.
        # Fetch their actual data before loading the in-process transport library.
        import xml.etree.ElementTree as ET
        sync_configuration()
        apply_model_settings(model)
        model.export_to_xml()
        chain=chain_file or openmc.config.get('chain_file')
        names=[]
        if chain:
            names=[n.attrib['name'] for n in ET.parse(chain).getroot().findall('nuclide')]
        ensure_for_model(Path.cwd(),config(),extra_nuclides=names)
        return original_operator(self,model,chain_file,*args,**kwargs)
    openmc.deplete.CoupledOperator.__init__=operator
    original_integrate=openmc.deplete.abc.Integrator.integrate
    def integrate(self,*args,**kwargs):
        bound=inspect.signature(original_integrate).bind_partial(self,*args,**kwargs)
        # 0.15.3 stopped writing rates by default. Tutorials explicitly read
        # those rates back; retain actual calculated rates without changing physics.
        if 'write_rates' not in bound.arguments:kwargs['write_rates']=True
        return original_integrate(self,*args,**kwargs)
    openmc.deplete.abc.Integrator.integrate=integrate
