"""Validated tally mutations; native XML and declarative rebuild sources stay aligned.

Runs only in the OpenMC SDK worker. Stage/parse before replacing inputs; never
rewrite physics sections through a full native-model XML round trip.
"""
from copy import deepcopy
import hashlib
import fcntl
import shutil
import uuid
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET


def _filters(specs, meshes):
    import openmc
    result = []
    for spec in specs:
        kind = spec['type']
        if kind == 'energy':item = openmc.EnergyFilter(spec['bins'])
        elif kind == 'material':item = openmc.MaterialFilter(spec['bins'])
        elif kind == 'particle':item = openmc.ParticleFilter(spec['bins'])
        elif kind == 'mesh':
            identifier = spec.get('mesh_id')
            mesh = meshes.get(identifier) if identifier is not None else None
            if mesh is not None:
                if (type(mesh).__name__ != 'RegularMesh' or list(mesh.dimension) != list(spec['dimension'])
                        or list(mesh.lower_left) != list(spec['lower_left'])
                        or list(mesh.upper_right) != list(spec['upper_right'])):
                    raise ValueError(f'mesh_id={identifier}已被不同网格定义使用；请选择新mesh_id，不改动其它tally的网格')
            else:
                mesh = openmc.RegularMesh(mesh_id=identifier)
                mesh.dimension = spec['dimension'];mesh.lower_left = spec['lower_left'];mesh.upper_right = spec['upper_right']
            meshes[int(mesh.id)] = mesh
            item = openmc.MeshFilter(mesh)
        else:raise ValueError('不支持的filter类型：' + str(kind))
        result.append(item)
    return result


def _spec(tally):
    """Lossless projection for the filter types supported by public mutation tools."""
    result = {'id': int(tally.id), 'name': tally.name, 'scores': list(tally.scores)}
    if tally.nuclides:result['nuclides'] = list(tally.nuclides)
    filters = []
    for f in tally.filters:
        kind = type(f).__name__
        if kind in ('EnergyFilter', 'MaterialFilter', 'ParticleFilter'):
            values = f.values if kind == 'EnergyFilter' else f.bins
            bins = values.tolist() if hasattr(values, 'tolist') else list(values)
            filters.append({'type': {'EnergyFilter':'energy','MaterialFilter':'material','ParticleFilter':'particle'}[kind], 'bins': bins})
        elif kind == 'MeshFilter' and type(f.mesh).__name__ == 'RegularMesh':
            filters.append({'type':'mesh','mesh_id':int(f.mesh.id), 'dimension':[int(v) for v in f.mesh.dimension],
                            'lower_left':[float(v) for v in f.mesh.lower_left], 'upper_right':[float(v) for v in f.mesh.upper_right]})
        else:raise ValueError('此过滤器不能无损写回声明式源：'+kind+'；原算例未修改')
    result['filters'] = filters
    return result


def _mutate_locked(params):
    import openmc
    from openmc_model_spec import checked_declarative_spec, build, save_json
    from openmc_parameters import load_model
    from openmc_worker import filter_summary
    operation = params.get('operation', 'add')
    baseline = checked_declarative_spec()
    model = load_model()
    tallies = model.tallies or openmc.Tallies()
    existing = {int(t.id):t for t in tallies}
    identifier = params.get('tally_id')
    if identifier is None:identifier = max(existing, default=0)+1
    previous = existing.get(identifier)
    if operation == 'add' and previous is not None:raise ValueError(f'tally_id={identifier}已存在；修改请用openmc_update_tally')
    if operation != 'add' and previous is None:raise ValueError(f'tally_id={identifier}不存在；可用编号：'+','.join(map(str,sorted(existing))))
    meshes = {int(f.mesh.id):f.mesh for t in tallies for f in t.filters if hasattr(f,'mesh')}
    # One mesh ID space includes settings (entropy/UFS/source), not only tallies.
    for path in ([Path('model.xml')] if Path('model.xml').exists() else [Path('settings.xml'),Path('tallies.xml')]):
        if path.is_file():
            for element in ET.parse(path).getroot().iter('mesh'):
                if element.get('id') is not None:
                    mesh=openmc.MeshBase.from_xml_element(element)
                    meshes[int(mesh.id)]=mesh

    replacement = None
    if operation != 'delete':
        replacement = deepcopy(previous) if previous is not None else openmc.Tally(tally_id=identifier)
        if params.get('scores') is not None:replacement.scores = params['scores']
        if params.get('name') is not None:replacement.name = params['name']
        if params.get('filters') is not None:replacement.filters = _filters(params['filters'], meshes)
    updated = openmc.Tallies([replacement if int(t.id)==identifier else t for t in tallies if operation!='delete' or int(t.id)!=identifier])
    if operation=='add':updated.append(replacement)
    import openmc_scores
    for t in updated:
        invalid, _ = openmc_scores.check(list(t.scores))
        if invalid:raise ValueError('score不在OpenMC可识别列表：' + ','.join(invalid))
    materials = {int(m.id) for m in model.materials}
    for t in updated:
        for f in t.filters:
            if type(f).__name__ == 'MaterialFilter':
                missing = sorted(set(int(v) for v in f.bins)-materials)
                if missing:raise ValueError(f'tally {t.id} MaterialFilter引用不存在的材料编号{missing}；已定义材料：{sorted(materials)}；未写入输入')
    here = Path.cwd(); before = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in here.glob('*.xml')}
    with staging_directory(here.parent) as tmp:
        stage = Path(tmp)
        if baseline is not None:
            spec = deepcopy(baseline);spec['tallies'] = [_spec(t) for t in updated]
            marker = json.loads((here/'mc-case.json').read_text())
            try:
                os.chdir(stage);build(spec)
            finally:os.chdir(here)
            generated = json.loads((stage/'mc-case.json').read_text());marker.update(generated);save_json(stage/'mc-case.json',marker)
            # The existing, trusted rebuild script is refreshed by the normal parameter path;
            # here copy the current builder and validation definitions as one rebuild set.
            from openmc_model_spec import _copy_validation_package
            shutil.copy2(Path(__file__).with_name('openmc_model_spec.py'),stage/'build_model.py');_copy_validation_package(stage)
            names = [*generated['model_xml_sha256'], 'model-spec.json','build_model.py','mc-case.json']
        else:
            updated.export_to_xml(stage/'tallies.xml')
            openmc.Tallies.from_xml(stage/'tallies.xml')
            if (here/'model.xml').exists():
                tree = ET.parse(here/'model.xml');root = tree.getroot();old = root.find('tallies');new = ET.parse(stage/'tallies.xml').getroot()
                if old is None:root.append(new)
                else:
                    index=list(root).index(old);root.remove(old);root.insert(index,new)
                tree.write(stage/'model.xml',encoding='utf-8',xml_declaration=True);names=['model.xml']
            else:names=['tallies.xml']
        if baseline is None:
            # XML parser reads all references together. Data resources remain at
            # their original paths; this is an API parse, not a transport run.
            for path in here.glob('*.xml'):
                if path.name not in names:shutil.copy2(path,stage/path.name)
            try:
                os.chdir(stage)
                load_model()
            finally:os.chdir(here)
        from openmc_scientific_checks import safe_assess, advisories_warnings
        model.tallies = updated
        science = safe_assess(model)
        publish_set(stage, here, names, baseline is not None)
    after = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in here.glob('*.xml')}
    def description(t):
        return None if t is None else {'id':int(t.id),'name':t.name,'scores':list(t.scores),'filters':[filter_summary(f) for f in t.filters]}
    return {'scientific_checks':science,'warnings':advisories_warnings(science),
            'operation':operation,'tally_id':identifier,'tally_count':len(updated), 'before':description(previous),'after':description(replacement),
            'name':replacement.name if replacement else None,'scores':list(replacement.scores) if replacement else [],
            'filters':[filter_summary(f) for f in replacement.filters] if replacement else [],
            'changed_files':sorted(n for n in set(before)|set(after) if before.get(n)!=after.get(n)),
            'declarative_source_synchronized':baseline is not None,'input_file':'model.xml' if (here/'model.xml').exists() else 'tallies.xml',
            'note':'仅修改算例输入，已提交作业快照不变；重新计算才有新计分结果。'}


def publish_set(stage, target, names, validation_package=False):
    """Rollback every owned target on publication failure; retain failed recovery.

    The marker is installed last. Readers which don't use the mutation lock
    must still check source hashes; a mixed-generation observation fails closed.
    """
    if validation_package:names=[n for n in names if n!='mc-case.json']+['model_validation','mc-case.json']
    backup=stage/'.backup';backup.mkdir()
    for name in names:
        source=target/name
        if source.is_dir():shutil.copytree(source,backup/name)
        elif source.exists():shutil.copy2(source,backup/name)
    try:
        for name in names:
            if name=='model_validation':
                shutil.rmtree(target/name,ignore_errors=True)
                shutil.copytree(stage/name,target/name)
            else:os.replace(stage/name,target/name)
    except Exception as original:
        try:
            for name in names:
                dst=target/name;src=backup/name
                if dst.is_dir():shutil.rmtree(dst)
                elif dst.exists():dst.unlink()
                if src.is_dir():shutil.copytree(src,dst)
                elif src.exists():shutil.copy2(src,dst)
        except Exception as recovery:
            preserved=target.parent/('.tally-recovery-'+uuid.uuid4().hex)
            try:os.rename(stage,preserved)
            except OSError:preserved=stage
            raise RecoveryFailure('计分发布和回滚均失败；请停止修改，完整恢复现场='+str(preserved)) from recovery
        raise original


def mutate(params):
    # Stable external lock: never copied into inputs or unlinked while peers wait.
    directory=Path(tempfile.gettempdir())/('mc-tally-locks-'+str(os.getuid()))
    directory.mkdir(mode=0o700,exist_ok=True)
    key=hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()
    with (directory/(key+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _mutate_locked(params)


class RecoveryFailure(RuntimeError):
    pass


@contextmanager
def staging_directory(parent):
    stage=Path(tempfile.mkdtemp(prefix='.tally-stage-',dir=parent))
    preserve=False
    try:yield stage
    except RecoveryFailure:
        preserve=True
        raise
    finally:
        if not preserve:shutil.rmtree(stage,ignore_errors=True)
