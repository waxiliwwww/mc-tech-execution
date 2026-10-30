"""Only registered official IDs/steps are executable; caller-supplied scripts are forbidden."""
from __future__ import annotations
import argparse
import contextlib
import json
from pathlib import Path
import runpy
import sys
import xml.etree.ElementTree as ET

from official_catalog import definition, verify_bundled_sources
from openmc_runtime import config, ROOT
from nuclear_data import ensure_for_model


class ModelPrepared(BaseException):pass


def prepare(item, parameters):
    import openmc
    source = verify_bundled_sources(item)
    if item['source_group'] == 'api':
        factory = getattr(openmc.examples, item['factory'])
        import inspect,hashlib
        installed=Path(inspect.getsourcefile(factory))
        if hashlib.sha256(installed.read_bytes()).hexdigest()!=hashlib.sha256((source/item['source_file']).read_bytes()).hexdigest():
            raise ValueError('运行环境的openmc.examples源码与固定官方版本不一致，未生成输入')
        model = factory(**parameters.get('factory', {}))
        model.export_to_xml()
        if model.plots:model.plots.export_to_xml()
    else:
        # Some official scripts solve at module scope. Stop exactly before their
        # first calculation; computation is an explicit later persistent step.
        def stop_run(*args, **kwargs):raise ModelPrepared()
        def stop_model_run(self, *args, **kwargs):
            self.export_to_xml();raise ModelPrepared()
        previous_argv=sys.argv
        filename=Path(item['source_file']).name
        sys.argv=[filename]+(['--generate'] if item['id']=='source-assembly' else [])
        previous_compiled_init=openmc.CompiledSource.__init__
        if item['workflow']=='compiled_source':
            default_library='build/libparameterized_source.so' if item['id']=='source-parameterized-custom-source' else 'build/libsource.so'
            def compiled_init(self,library=None,*args,**kwargs):
                return previous_compiled_init(self,library or default_library,*args,**kwargs)
            openmc.CompiledSource.__init__=compiled_init
        previous_run = openmc.run; previous_model_run = openmc.Model.run
        openmc.run = stop_run;openmc.Model.run = stop_model_run
        try:
            try:runpy.run_path(str(Path(item['source_file']).name), run_name='__main__')
            except ModelPrepared:pass
        finally:
            openmc.run=previous_run;openmc.Model.run=previous_model_run;sys.argv=previous_argv
            openmc.CompiledSource.__init__=previous_compiled_init
    settings_path = Path('model.xml') if Path('model.xml').exists() else Path('settings.xml')
    tree = ET.parse(settings_path);root = tree.getroot()
    settings = root.find('settings') if root.tag == 'model' else root
    before = {key:settings.findtext(key) for key in ('run_mode','particles','batches','inactive','seed')}
    mode = settings.findtext('run_mode', 'eigenvalue')
    for key,value in parameters.items():
        if key not in ('particles','batches','inactive','seed'):continue
        if key == 'inactive' and mode != 'eigenvalue':raise ValueError('固定源/特殊模式不接受inactive覆盖')
        node=settings.find(key)
        if node is None:node=ET.SubElement(settings,key)
        node.text=str(value)
    if parameters:tree.write(settings_path,encoding='utf-8',xml_declaration=True)
    after = {key:settings.findtext(key) for key in before}
    Path('prepared-example.json').write_text(json.dumps({'example_id':item['id'],'source_revision':item['source_revision'],
        'original_settings':before,'effective_settings':after,'overrides':parameters,
        'note':'只准备输入；尚未执行计算。'},ensure_ascii=False,indent=2)+'\n')
    return {'prepared':True,'settings':after}


def compile_source(item):
    import shutil
    import subprocess
    cfg=config()
    prefix=Path(cfg['openmc_command'][0]).resolve().parent.parent
    cmake=cfg.get('cmake_command') or [shutil.which('cmake') or 'cmake']
    subprocess.run(cmake+['-S','.', '-B','build','-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_PREFIX_PATH='+str(prefix),
        '-DCMAKE_PROJECT_INCLUDE='+str(ROOT/'mcp/cmake/openmc-example-dependencies.cmake')],check=True)
    subprocess.run(cmake+['--build','build','--parallel','2'],check=True)
    libraries=list(Path('build').glob('*.so'))
    if not libraries:raise ValueError('编译未产生共享源库，不能继续生成/计算')
    return {'compiled':True,'libraries':[str(x) for x in libraries]}


def main():
    p=argparse.ArgumentParser();p.add_argument('example_id');p.add_argument('step');p.add_argument('--parameters',default='{}');p.add_argument('--threads',type=int,default=1);p.add_argument('model_file',nargs='?');a=p.parse_args()
    item=definition(a.example_id)
    if a.step=='prepare':value=prepare(item,json.loads(a.parameters))
    elif a.step=='compile':value=compile_source(item)
    elif a.step=='data':value=ensure_for_model(Path.cwd(),config())
    elif a.step=='postprocess':
        from script_workflows import postprocess
        value=postprocess(item)
    elif a.step in ('execute','restart') and item['workflow'] in ('depletion','depletion_restart','pulsed_source'):
        from script_workflows import execute_script
        value=execute_script(item,a.step,json.loads(a.parameters))
    elif a.step=='execute' and item['workflow']=='notebook':
        from notebook_runner import execute_notebook
        value=execute_notebook(item)
    else:raise ValueError('未注册的官方例题步骤')
    Path('step-'+a.step+'.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(value,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
