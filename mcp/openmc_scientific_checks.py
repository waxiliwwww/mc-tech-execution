"""Bounded scientific-input advisories. Never change physics or reject a model.

Inspect only library metadata (up to 64 files / 2s between reads), not reaction
arrays. Report partial/unavailable instead of claiming complete verification.
These checks neither assess source convergence nor validate physical results.
"""
from pathlib import Path
import math, os, time
import xml.etree.ElementTree as ET

MAX_DATA_FILES = 64
METADATA_SECONDS = 2.0
DEFAULT_TEMPERATURE_K = 293.6  # OpenMC 0.16 settings specification.


def energy_advisory(model, filters=None):
    if model.settings.energy_mode != 'multi-group':
        return {'status':'not_applicable','warnings':[]}
    import h5py
    import numpy as np
    path = model.materials.cross_sections or os.environ.get('OPENMC_MG_CROSS_SECTIONS')
    result = {'status':'unavailable','library_path':str(path) if path else None,'checks':[],'warnings':[]}
    try:
        with h5py.File(path,'r') as library:edges=np.asarray(library.attrs['group structure'],dtype=float)
        if edges.ndim!=1 or len(edges)<2 or not np.all(np.isfinite(edges)) or not np.all(np.diff(edges)>0):
            raise ValueError('MGXS群边界无效')
    except (OSError,ValueError,TypeError,KeyError) as exc:
        result['warnings']=['多群能量分箱未核实：无法读取MGXS群边界（'+type(exc).__name__+'）；不要将零值箱直接解释成物理零。']
        return result
    result.update(status='checked',library_groups=len(edges)-1,group_edges_ev=edges[:1000].tolist(),group_edges_total=len(edges),group_edges_truncated=len(edges)>1000)
    if len(edges)>1000:result['warnings'].append('MGXS群边界预览已截断；完整群结构见library_path，不能将预览当完整分箱。')
    selected=filters if filters is not None else [f for t in model.tallies for f in t.filters]
    seen=set()
    for f in selected:
        if type(f).__name__ not in ('EnergyFilter','EnergyoutFilter') or f.id in seen:continue
        seen.add(f.id);requested=np.unique(np.asarray(f.bins,dtype=float).ravel())
        unmatched=[float(v) for v in requested if not np.any(np.isclose(edges,v,rtol=1e-8,atol=1e-12))]
        state='mismatch' if unmatched else 'aligned' if len(requested)==len(edges) else 'aligned_subset'
        result['checks'].append({'filter_id':int(f.id),'status':state,'requested_bins':len(requested)-1,
                                 'unmatched_edges_ev':unmatched[:50],'unmatched_edges_count':len(unmatched)})
        if unmatched:
            result['warnings'].append(f'能量filter {f.id}边界与MGXS {len(edges)-1}群结构不一致；多群输运不提供任意连续能谱分辨率，零值箱可能由分箱错配或统计不足造成。请核对group_edges_ev；未自动改分箱。')
    result['fully_aligned'] = all(c['status']!='mismatch' for c in result['checks']) if result['checks'] else None
    if result['fully_aligned'] is False:result['status']='mismatch'
    return result


def temperature_advisory(model):
    import h5py
    import openmc
    started=time.monotonic();warnings=[];records=[];cache={};opened=0
    if model.settings.energy_mode=='multi-group':
        return {'status':'not_assessed_multigroup','warnings':[],
                'note':'多群库温度适用性未评估；不套用连续能温度规则。'}
    if model.settings.properties_file:
        return {'status':'partial','metadata_complete':False,'applicability_verified':False,
                'properties_file':str(model.settings.properties_file),'materials':[],
                'warnings':['settings.properties_file可能覆盖cell温度和材料密度；当前未读取该文件，有效温度未核实，不能据此宣称使用引擎默认温度。']}
    configured=model.settings.temperature or {};default=configured.get('default',DEFAULT_TEMPERATURE_K)
    index=model.materials.cross_sections or os.environ.get('OPENMC_CROSS_SECTIONS')
    catalog={};catalog_error=None
    try:
        index=Path(index).resolve();tree=ET.parse(index).getroot();base=Path(tree.findtext('directory') or index.parent)
        if not base.is_absolute():base=index.parent/base
        for entry in tree.findall('library'):
            if entry.get('type') in ('neutron','thermal'):
                for name in entry.get('materials','').split():catalog[(entry.get('type'),name)]=base/entry.get('path','')
    except (TypeError,OSError,ValueError,ET.ParseError) as exc:catalog_error=type(exc).__name__
    requests={}
    for cell in model.geometry.get_all_material_cells().values():
        fills=cell.fill if isinstance(cell.fill,list) else [cell.fill]
        temps=cell.temperature
        for i,material in enumerate(fills):
            if material is None:continue
            candidates = list(temps) if temps is not None and hasattr(temps, '__iter__') else [temps]
            if len(fills)>1 and len(candidates)==len(fills):candidates=[candidates[i]]
            for value in candidates:
                if value is not None:source='cell'
                elif material.temperature is not None:value=material.temperature;source='material'
                else:value=default;source='settings_default' if 'default' in configured else 'engine_default'
                requests[(material.id,float(value),source)]=material
    for (identifier,requested,source),material in list(requests.items())[:200]:
        if source=='engine_default':
            warnings.append(f'材料 {identifier}（{material.name}）及其cell未显式声明温度，将使用引擎默认{default:g} K；请确认，不能从材料名称或密度推断温度。')
        keys=[('neutron',n) for n in material.get_nuclides()]+[('thermal',s.get('name')) for s in material.to_xml_element().findall('sab')]
        checked=[];unavailable=[]
        for kind,name in keys:
            path=catalog.get((kind,name));key=(str(path),name)
            if key not in cache:
                if path is None or opened>=MAX_DATA_FILES or time.monotonic()-started>METADATA_SECONDS:
                    cache[key]=None
                else:
                    opened+=1
                    try:
                        with h5py.File(path,'r') as data:
                            kts=data[name]['kTs']
                            cache[key]=sorted(float(kts[n][()])/openmc.data.K_BOLTZMANN for n in kts)
                            if not all(math.isfinite(v) and v>0 for v in cache[key]):cache[key]=None
                    except (OSError,KeyError,TypeError,ValueError):cache[key]=None
            available=cache[key]
            if not available:unavailable.append(name);continue
            method=configured.get('method','nearest');tolerance=configured.get('tolerance',10.0)
            supported=(min(abs(v-requested) for v in available)<=tolerance if method=='nearest' else
                       available[0]<=requested<=available[-1] or min(abs(v-requested) for v in available)<=tolerance)
            # Multipole and other advanced treatments cannot be inferred solely
            # from tabulated kTs: retain the bounds, but never claim unsupported.
            if configured.get('multipole'):supported=None
            checked.append({'name':name,'type':kind,'available_k':available,'temperature_supported_by_metadata':supported})
            if supported is False:warnings.append(f'材料 {identifier} 请求{requested:g} K，与{name}的{method}温度数据/容差不匹配；请核对库和设置，未自动改温度。')
        records.append({'material_id':identifier,'requested_k':requested,'source':source,
                        'method':configured.get('method','nearest'),'datasets':checked,'unavailable':unavailable})
    complete=bool(records) and all(not r['unavailable'] for r in records) and len(requests)<=200
    if not complete:warnings.append('温度与截面库适用性未完全核实：可能缺少索引/元数据，或达到检查预算；API解析成功不等于温度数据可用。')
    return {'status':'metadata_checked' if complete else 'partial','metadata_complete':complete,'applicability_verified':False,'materials':records,
            'unreported_materials':max(0,len(requests)-200),'index_path':str(index) if index else None,'index_error':catalog_error,
            'metadata_files_opened':opened,'elapsed_ms':round((time.monotonic()-started)*1000,2),
            'budget':{'max_files':MAX_DATA_FILES,'seconds_between_reads':METADATA_SECONDS},'warnings':warnings[:100],
            'note':'只比较kTs元数据与nearest/interpolation设置；不证明加载完整、热物性一致或物理正确。高级multipole不判定。'}


def assess(model):
    return {'energy_groups':energy_advisory(model),'temperature':temperature_advisory(model)}


def assess_directory():
    import openmc
    try:
        model=openmc.Model.from_model_xml('model.xml') if Path('model.xml').is_file() else openmc.Model.from_xml()
        return safe_assess(model)
    except Exception as exc:
        return {'status':'unavailable','warnings':['科学输入检查未执行：模型或数据元信息不可读取（'+type(exc).__name__+'），不构成通过。']}


def advisories_warnings(result):
    if 'warnings' in result:return result['warnings']
    return [w for item in result.values() if isinstance(item,dict) for w in item.get('warnings',[])]


def safe_assess(model):
    try:return assess(model)
    except Exception as exc:
        return {'status':'unavailable','warnings':['科学输入检查未完成（'+type(exc).__name__+'）；不构成通过，未修改输入。']}
