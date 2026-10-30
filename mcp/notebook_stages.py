"""Retain per-solve inputs and raw results before tutorial filenames are reused."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET
from nuclear_data import data_digest
from openmc_runtime import atomic_json,Error

INPUTS=('model.xml','geometry.xml','materials.xml','settings.xml','tallies.xml','plots.xml')


def semantic(element,omit_guard=False):
    return (element.tag,tuple(sorted(element.attrib.items())),(element.text or '').strip(),
            tuple(semantic(child,omit_guard) for child in element
                  if not (omit_guard and child.tag=='source_rejection_fraction')))


def cad_guard(directory: Path) -> dict:
    filename=directory/('model.xml' if (directory/'model.xml').exists() else 'settings.xml')
    before=filename.read_bytes();original_root=ET.fromstring(before)
    tree=ET.parse(filename);root=tree.getroot();settings=root.find('settings') if root.tag=='model' else root
    tag=settings.find('source_rejection_fraction')
    old=tag.text if tag is not None else '0.05 (OpenMC default)'
    if tag is None:tag=ET.SubElement(settings,'source_rejection_fraction')
    tag.text='0.00001'
    if semantic(original_root,True)!=semantic(root,True):
        raise Error('CAD兼容适配改变了守卫之外的输入，未启动计算')
    tree.write(filename,encoding='utf-8',xml_declaration=True)
    return {'setting':'source_rejection_fraction','before':old,'after':0.00001,
            'only_this_setting_changed':True,'before_xml_sha256':__import__('hashlib').sha256(before).hexdigest(),
            'after_xml_sha256':data_digest(filename),
            'note':'仅降低最低采样接受率的中止守卫；XML语义比较确认源分布、约束、粒子数和批次未改变。'}


class StageRecorder:
    def __init__(self,example_id):
        self.root=Path.cwd().resolve();self.example_id=example_id
        self.path=self.root/'notebook-stages.json'
        self.value={'example_id':example_id,'calculations':[],'reads':[]}
        atomic_json(self.path,self.value)

    def relative(self,path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.root):raise Error('Notebook阶段路径超出本次作业目录')
        return path.relative_to(self.root).as_posix()

    def copy(self,source,destination):
        if source.is_symlink():raise Error('阶段快照不接受符号链接')
        digest=data_digest(source);destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
        if data_digest(destination)!=digest:raise Error('阶段结果在复制期间改变，未登记有效快照')
        return {'path':self.relative(destination),'sha256':digest,'bytes':destination.stat().st_size}

    def checkpoint_inputs(self,stage,directory,label):
        files={}
        for name in INPUTS:
            path=directory/name
            if path.is_file():files[name]=self.copy(path,self.root/stage['directory']/label/name)
        return files

    def begin(self,kind,directory):
        directory=Path(directory).resolve()
        # Official Model.plot uses a private TemporarySession outside cwd.
        # Read its generated inputs, but always store snapshots under this job.
        progress=self.root/'notebook-progress.json'
        source=json.loads(progress.read_text()) if progress.exists() else {}
        identifier=f"{len(self.value['calculations'])+1:04d}"
        stage={'id':identifier,'kind':kind,'state':'running','directory':'stages/'+identifier,
               'original_cell_index':source.get('original_cell_index',source.get('cell')),
               'code_cell':source.get('code_cell'),'temporary_execution_directory':not directory.is_relative_to(self.root),'started_at':time.time(),'outputs':{},
               'existing_results':{p.name:[p.stat().st_size,p.stat().st_mtime_ns]
                                   for p in set(directory.glob('statepoint*.h5'))|set(directory.glob('summary.h5')) if p.is_file()}}
        stage['original_inputs']=self.checkpoint_inputs(stage,directory,'original-inputs')
        self.value['calculations'].append(stage);atomic_json(self.path,self.value)
        return stage

    def effective(self,stage,directory,check):
        stage['inputs']=self.checkpoint_inputs(stage,directory,'inputs');stage['preflight']=check
        resources={}
        for name in INPUTS:
            path=directory/name
            if not path.exists():continue
            root=ET.parse(path).getroot()
            references=[e.attrib['filename'] for e in root.iter() if e.attrib.get('filename','').endswith('.h5m')]
            references += [e.text for e in root.iter('cross_sections') if e.text]
            for reference in references:
                source=Path(reference);source=source if source.is_absolute() else directory/source
                if source.is_file():resources[reference]={'server_path':str(source.resolve()),'sha256':data_digest(source),'bytes':source.stat().st_size}
        stage['input_resources']=resources
        stage['snapshot_scope']='XML输入和每次原始结果已复制；几何网格/截面等外部资源记录身份，沿原作业及初配数据使用，不是完整独立重跑包。'
        settings=directory/('model.xml' if (directory/'model.xml').exists() else 'settings.xml')
        if settings.exists():
            root=ET.parse(settings).getroot()
            stage['run_mode']=root.findtext('.//run_mode','eigenvalue')
            stage['geometry_files']=sorted({e.attrib['filename'] for e in root.iter()
                                           if e.attrib.get('filename','').endswith('.h5m')})
        atomic_json(self.path,self.value)

    def properties(self,stage,label):
        import openmc.lib
        path=self.root/stage['directory']/('runtime-properties-'+label+'.h5')
        path.parent.mkdir(parents=True,exist_ok=True)
        openmc.lib.export_properties(str(path),output=False)
        stage['runtime_properties_'+label]={'path':self.relative(path),'sha256':data_digest(path)}
        atomic_json(self.path,self.value)

    def capture_statepoint(self,stage,filename):
        source=Path(filename).resolve()
        name=self.relative(source)
        stage['outputs'][name]={**self.copy(source,self.root/stage['directory']/'outputs'/name),'role':'statepoint'}
        atomic_json(self.path,self.value)

    def finish(self,stage,directory,state='completed',error=None):
        start=time.monotonic()
        # Copy raw solver products now, before the notebook can overwrite or
        # rename them. Each solve has its own geometry summary for later reading.
        files=sorted(set(directory.glob('statepoint*.h5'))|set(directory.glob('summary.h5')))
        for source in files:
            if source.is_file() and stage['existing_results'].get(source.name)!=[source.stat().st_size,source.stat().st_mtime_ns]:
                stage['outputs'][source.name]={**self.copy(source,self.root/stage['directory']/'outputs'/source.name),
                                               'role':'statepoint' if source.name.startswith('statepoint') else 'geometry_summary'}
        stage.update(state=state,finished_at=time.time(),snapshot_seconds=time.monotonic()-start)
        if error:stage['error']=type(error).__name__+': '+str(error)[:1000]
        atomic_json(self.path,self.value)

    def record_read(self,filename):
        if not isinstance(filename,(str,Path)):return
        path=Path(filename).resolve()
        if not path.is_file() or not path.is_relative_to(self.root):return
        digest=data_digest(path);matches=[]
        for stage in self.value['calculations']:
            if stage['state']!='completed':continue
            for output in stage['outputs'].values():
                if output['sha256']==digest:matches.append({'calculation_id':stage['id'],**output})
        progress=self.root/'notebook-progress.json'
        source=json.loads(progress.read_text()) if progress.exists() else {}
        self.value['reads'].append({'original_cell_index':source.get('original_cell_index',source.get('cell')),
                                   'code_cell':source.get('code_cell'),'requested_path':self.relative(path),
                                   'sha256':digest,'matched_snapshots':matches})
        atomic_json(self.path,self.value)
