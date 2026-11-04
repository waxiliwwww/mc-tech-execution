"""Versioned declarative OpenMC input; no user code execution or inferred physics.

This module can be copied next to model-spec.json and run with only OpenMC installed.
All coordinates are centimetres, energies eV and temperatures K.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import os
import shutil
import tempfile
import json
import math
from pathlib import Path
import re


def field(kind, description, **kw):
    return {'type': kind, 'description': description, **kw}


def obj(description, properties, required):
    return field('object', description, properties=properties, required=required, additionalProperties=False)


def array(description, items, minimum=0, maximum=10000, **kw):
    return field('array', description, items=items, minItems=minimum, maxItems=maximum, **kw)


ID = field('integer', '显式正整数编号；引用必须存在。', minimum=1)
NUMBER = field('number', '有限数值；单位取决于所在字段。')
POSITIVE = field('number', '有限正数。', exclusiveMinimum=0)
TEXT = field('string', '可读名称，不作为代码执行。', maxLength=200)
VEC3 = array('三维坐标 [x,y,z]，cm。', NUMBER, 3, 3)
VEC2 = array('二维坐标/节距 [x,y]，cm。', NUMBER, 2, 2)
NUCLIDE = obj('核素组成；全材料使用同一种原子/质量份额口径。', {
    'name': field('string', 'OpenMC核素名，例如 U235、O16。', minLength=1, maxLength=40),
    'fraction': POSITIVE}, ['name', 'fraction'])
MATERIAL = obj('显式组成的连续能材料；不自动推断化学式或富集度。', {
    'id': ID, 'name': TEXT, 'density': POSITIVE,
    'density_unit': field('string', '密度单位。', enum=['g/cm3','atom/b-cm','atom/cm3']),
    'percent_type': field('string', 'ao为原子份额，wo为质量份额；应用于所有核素。', enum=['ao','wo']),
    'nuclides': array('核素及其相对份额，由OpenMC按所选口径归一化。', NUCLIDE, 1, 500),
    'temperature': field('number', '温度 K；可运行范围还取决于核数据。', exclusiveMinimum=0),
    'sab': array('热散射名称，例如 c_H_in_H2O；必须有对应数据。', TEXT, 0, 20),
    'depletable': field('boolean', '是否可燃耗；燃耗仍需功率、时间步和链。'),
    'volume': field('number', '材料体积 cm³；由建模者提供，工具不推断。', exclusiveMinimum=0)},
    ['id','name','density','density_unit','percent_type','nuclides'])
SURFACE = obj('受控CSG面。参数名与OpenMC Python API一致，长度为cm。', {
    'id': ID, 'type': field('string', '面类型。', enum=['x-plane','y-plane','z-plane','x-cylinder','y-cylinder','z-cylinder','sphere']),
    'boundary': field('string', '边界条件；省略为transmission。', enum=['transmission','vacuum','reflective','white']),
    'parameters': obj('平面必须显式给对应坐标；圆柱/球须给r，省略中心坐标为0；不匹配参数报错。', {
        'x0': NUMBER,'y0':NUMBER,'z0':NUMBER,'r':POSITIVE}, [])}, ['id','type','parameters'])
FILL = obj('填充材料、宇宙或格架；void表示真空材料。', {
    'type': field('string', '引用类别。', enum=['material','universe','lattice','void']), 'id':ID}, ['type'])
CELL = obj('栅元属于一个宇宙，region引用面编号。', {
    'id':ID,'name':TEXT,'universe':field('integer','所属宇宙编号；根宇宙可以是0。',minimum=0),
    'region':field('string','OpenMC区域表达式：负面号=内部，空格=交，|=并，~=补，支持括号；不执行Python。',minLength=1,maxLength=10000),
    'fill':FILL}, ['id','universe','region','fill'])
LATTICE = obj('二维矩形格架，可重复引用组件/燃料棒宇宙。', {
    'id':ID,'name':TEXT,'lower_left':VEC2,'pitch':array('两个方向的正节距，cm。',POSITIVE,2,2),
    'universes':array('按y从上到下排列的行；每行x从左到右，元素为宇宙编号，各行等长。', array('一行宇宙编号。',field('integer','宇宙编号。',minimum=0),1,100),1,100),
    'outer':field('integer','格架范围外的宇宙编号；省略则外部由几何边界限制。',minimum=0)}, ['id','lower_left','pitch','universes'])
SOURCE = obj('中子初始源；点或均匀长方体，方向各向同性。', {
    'type':field('string','源空间分布。',enum=['point','box']), 'point':VEC3,'lower_left':VEC3,'upper_right':VEC3,
    'strength':field('number','源相对强度，省略为1。',exclusiveMinimum=0),
    'energy_ev':field('number','可选单能源能量 eV；省略保留OpenMC默认源能谱。',exclusiveMinimum=0)}, ['type'])
FILTER = obj('基础计分过滤器。', {
    'type':field('string','能量、材料或规则网格过滤。',enum=['energy','material','mesh']),
    'bins':array('energy为eV边界，material为材料编号。',NUMBER,1,10000),
    'dimension':array('规则网格三个方向的箱数。',field('integer','每方向正整数箱数。',minimum=1),3,3),
    'lower_left':VEC3,'upper_right':VEC3}, ['type'])
TALLY = obj('统计量定义；未定义的量不会凭空生成。', {
    'id':ID,'name':TEXT,'scores':array('OpenMC计分名，例如 flux、fission、nu-fission。',TEXT,1,20),
    'nuclides':array('可选核素计分列表，例如 total、U235。',TEXT,1,500),
    'filters':array('过滤器组合；省略为空。',FILTER,0,8)}, ['id','scores'])
SETTINGS = obj('运行设置；低统计设置只用于工程调试。', {
    'run_mode':field('string','本征值或固定源。',enum=['eigenvalue','fixed source']),
    'particles':field('integer','每代粒子数。',minimum=1,maximum=100000000),
    'batches':field('integer','批次数；本征模式至少保留两个活跃批次。',minimum=2,maximum=1000000),
    'inactive':field('integer','非活跃批次数；固定源必须为0。',minimum=0),
    'generations_per_batch':field('integer','每批代数；省略为1。',minimum=1),
    'seed':field('integer','随机种子；省略为1。',minimum=1), 'source':SOURCE},
    ['run_mode','particles','batches','inactive','source'])
MODEL_SCHEMA = obj('OpenMC model-spec/v1。组合模型，不执行调用方Python；专业适用性需另行确认。', {
    'schema_version':field('string','结构版本。',enum=['1']), 'name':TEXT,
    'materials':array('材料定义。',MATERIAL,1,1000), 'surfaces':array('CSG面定义。',SURFACE,1,10000),
    'cells':array('栅元及宇宙归属。',CELL,1,10000), 'lattices':array('可选二维矩形格架。',LATTICE,0,100),
    'root_universe':field('integer','根宇宙编号，必须含栅元。',minimum=0),
    'settings':SETTINGS, 'tallies':array('可选计分定义。',TALLY,0,100)},
    ['schema_version','name','materials','surfaces','cells','root_universe','settings'])


def _shape(value, schema, path='$'):
    """Validate the deliberately small schema subset without non-OpenMC dependencies."""
    kind = schema['type']
    valid = {'object': lambda: isinstance(value,dict), 'array':lambda:isinstance(value,list),
             'string':lambda:isinstance(value,str),'integer':lambda:type(value) is int,
             'number':lambda:type(value) in (int,float) and math.isfinite(value),
             'boolean':lambda:type(value) is bool}[kind]()
    if not valid:raise ValueError(path+' 类型错误或非有限值')
    if 'enum' in schema and value not in schema['enum']:raise ValueError(path+' 不支持的值')
    if kind == 'object':
        for key in schema['required']:
            if key not in value:raise ValueError(path+'.'+key+' 缺少必需字段')
        for key,item in value.items():
            if key not in schema['properties']:raise ValueError(path+'.'+key+' 不支持的字段')
            _shape(item,schema['properties'][key],path+'.'+key)
    if kind in ('array','string'):
        small,big=('minItems','maxItems') if kind=='array' else ('minLength','maxLength')
        if len(value)<schema.get(small,0) or len(value)>schema.get(big,10**9):raise ValueError(path+' 长度超限')
    if kind=='array':
        for i,item in enumerate(value):_shape(item,schema['items'],path+f'[{i}]')
    if kind in ('number','integer'):
        if value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf) or value<=schema.get('exclusiveMinimum',-math.inf):raise ValueError(path+' 数值超范围')


def validate_spec(spec):
    _shape(spec,MODEL_SCHEMA)
    ids={}
    for kind in ('materials','surfaces','cells','lattices','tallies'):
        entries=spec.get(kind,[]); ids[kind]={e['id']:e for e in entries}
        if len(ids[kind])!=len(entries):raise ValueError(kind+' 编号重复')
    for mat in spec['materials']:
        names=[n['name'] for n in mat['nuclides']]
        if len(set(names))!=len(names):raise ValueError('材料核素重复')
    universes={c['universe'] for c in spec['cells']}
    if spec['root_universe'] not in universes:raise ValueError('根宇宙不存在')
    if universes & ids['lattices'].keys():raise ValueError('宇宙和格架编号不可冲突')
    graph={u:[] for u in universes}
    def edge(parent,kind,identifier):
        pool=ids['materials'] if kind=='material' else universes if kind=='universe' else ids['lattices']
        if identifier not in pool:raise ValueError(f'{kind} 引用不存在：{identifier}')
        if kind=='universe':graph[parent].append(identifier)
        if kind=='lattice':
            lat=ids['lattices'][identifier]
            rows=lat['universes']
            if len({len(r) for r in rows})!=1:raise ValueError('格架每行长度必须一致')
            for target in [v for row in rows for v in row]+([lat['outer']] if 'outer' in lat else []):edge(parent,'universe',target)
    for cell in spec['cells']:
        expr=cell['region']
        if not re.fullmatch(r'[\d\s+\-~|()]+',expr):raise ValueError('region仅接受面编号与布尔区域运算')
        for surface_id in re.findall(r'\d+',expr):
            if int(surface_id) not in ids['surfaces']:raise ValueError('region面引用不存在：'+surface_id)
        fill=cell['fill']
        if fill['type']=='void':
            if 'id' in fill:raise ValueError('void不能带id')
        elif 'id' not in fill:raise ValueError('fill缺少引用id')
        else:edge(cell['universe'],fill['type'],fill['id'])
    used_lattices={c['fill']['id'] for c in spec['cells'] if c['fill']['type']=='lattice'}
    if used_lattices!=set(ids['lattices']):raise ValueError('存在未引用格架，拒绝静默忽略')
    surface_keys={'x-plane':{'x0'},'y-plane':{'y0'},'z-plane':{'z0'},'x-cylinder':{'y0','z0','r'},'y-cylinder':{'x0','z0','r'},'z-cylinder':{'x0','y0','r'},'sphere':{'x0','y0','z0','r'}}
    for surface in spec['surfaces']:
        allowed=surface_keys[surface['type']];values=surface['parameters']
        if set(values)-allowed:raise ValueError('surface参数与类型不匹配')
        required=allowed if surface['type'].endswith('plane') else {'r'}
        if not required<=values.keys():raise ValueError('surface缺少必需坐标/半径')
    done=set(); visiting=set()
    def visit(u,depth=0):
        if depth>80:raise ValueError('宇宙引用深度超限')
        if u in visiting:raise ValueError('宇宙/格架循环引用')
        if u in done:return
        visiting.add(u)
        for child in graph[u]:visit(child,depth+1)
        visiting.remove(u);done.add(u)
    visit(spec['root_universe'])
    if done!=universes:raise ValueError('存在根模型不可达的宇宙，拒绝静默忽略')
    settings=spec['settings']
    if settings['batches']-settings['inactive']<2:raise ValueError('至少需要两个活跃批次')
    if settings['run_mode']=='fixed source' and settings['inactive']!=0:raise ValueError('固定源inactive必须为0')
    source=settings['source']
    if source['type']=='point':
        if 'point' not in source or set(source)-{'type','point','energy_ev','strength'}:raise ValueError('点源只接受point与可选energy_ev')
    else:
        if not {'lower_left','upper_right'}<=source.keys() or set(source)-{'type','lower_left','upper_right','energy_ev','strength'}:raise ValueError('盒源需要lower_left/upper_right')
        if any(a>=b for a,b in zip(source['lower_left'],source['upper_right'])):raise ValueError('盒源下界必须小于上界')
    total_tally_values=0
    for tally in spec.get('tallies',[]):
        tally_values=len(tally['scores'])*len(tally.get('nuclides',['total']))
        kinds=[f['type'] for f in tally.get('filters',[])]
        if len(kinds)!=len(set(kinds)):raise ValueError('同一tally过滤类型不可重复')
        for f in tally.get('filters',[]):
            if f['type']=='mesh':
                if not {'dimension','lower_left','upper_right'}<=f.keys() or 'bins' in f:raise ValueError('网格过滤需要dimension/lower_left/upper_right')
                tally_values*=math.prod(f['dimension'])
                if any(a>=b for a,b in zip(f['lower_left'],f['upper_right'])):raise ValueError('网格下界必须小于上界')
            else:
                if set(f)!={'type','bins'}:raise ValueError('energy/material过滤只接受bins')
                bins=f['bins']
                tally_values*=len(bins)-1 if f['type']=='energy' else len(bins)
                if f['type']=='energy' and (len(bins)<2 or bins[0]<0 or any(a>=b for a,b in zip(bins,bins[1:]))):raise ValueError('能量边界须非负且严格递增')
                if f['type']=='material' and any(type(b) is not int or b not in ids['materials'] for b in bins):raise ValueError('材料过滤引用不存在')
        total_tally_values+=tally_values
        if total_tally_values>1000000:raise ValueError('组合计分值数量超过建模接口上限1000000；请缩减过滤/核素/score组合')
    return spec


def build(spec):
    """Create inputs in cwd using public OpenMC APIs, then parse the actual output."""
    validate_spec(spec)
    import openmc
    materials={}
    for s in spec['materials']:
        m=openmc.Material(material_id=s['id'],name=s['name']);m.set_density(s['density_unit'],s['density'])
        for n in s['nuclides']:m.add_nuclide(n['name'],n['fraction'],s['percent_type'])
        for sab in s.get('sab',[]):m.add_s_alpha_beta(sab)
        if 'temperature' in s:m.temperature=s['temperature']
        if 'volume' in s:m.volume=s['volume']
        m.depletable=s.get('depletable',False);materials[m.id]=m
    constructors={'x-plane':(openmc.XPlane,{'x0'}),'y-plane':(openmc.YPlane,{'y0'}),'z-plane':(openmc.ZPlane,{'z0'}),
                  'x-cylinder':(openmc.XCylinder,{'y0','z0','r'}),'y-cylinder':(openmc.YCylinder,{'x0','z0','r'}),
                  'z-cylinder':(openmc.ZCylinder,{'x0','y0','r'}),'sphere':(openmc.Sphere,{'x0','y0','z0','r'})}
    surfaces={}
    for s in spec['surfaces']:
        cls,allowed=constructors[s['type']]
        if set(s['parameters'])-allowed:raise ValueError('surface参数与类型不匹配')
        if 'r' in allowed and 'r' not in s['parameters']:raise ValueError('曲面必须显式提供r')
        surfaces[s['id']]=cls(surface_id=s['id'],boundary_type=s.get('boundary','transmission'),**s['parameters'])
    universes={i:openmc.Universe(universe_id=i) for i in {c['universe'] for c in spec['cells']}}
    lattices={}
    for s in spec.get('lattices',[]):
        lat=openmc.RectLattice(lattice_id=s['id'],name=s.get('name',''));lat.lower_left=s['lower_left'];lat.pitch=s['pitch']
        lat.universes=[[universes[i] for i in row] for row in s['universes']]
        if 'outer' in s:lat.outer=universes[s['outer']]
        lattices[s['id']]=lat
    for s in spec['cells']:
        f=s['fill'];fill=None if f['type']=='void' else {'material':materials,'universe':universes,'lattice':lattices}[f['type']][f['id']]
        cell=openmc.Cell(cell_id=s['id'],name=s.get('name',''),fill=fill,region=openmc.Region.from_expression(s['region'],surfaces))
        universes[s['universe']].add_cell(cell)
    geometry=openmc.Geometry(universes[spec['root_universe']])
    settings=openmc.Settings();s=spec['settings']
    settings.run_mode=s['run_mode'];settings.particles=s['particles'];settings.batches=s['batches'];settings.inactive=s['inactive'];settings.seed=s.get('seed',1);settings.generations_per_batch=s.get('generations_per_batch',1)
    src=s['source'];space=openmc.stats.Point(src['point']) if src['type']=='point' else openmc.stats.Box(src['lower_left'],src['upper_right'])
    settings.source=openmc.IndependentSource(space=space,strength=src.get('strength',1.0),**({'energy':openmc.stats.Discrete([src['energy_ev']],[1.0])} if 'energy_ev' in src else {}))
    tallies=openmc.Tallies();next_filter_id=1
    for s in spec.get('tallies',[]):
        tally=openmc.Tally(tally_id=s['id'],name=s.get('name',''));tally.scores=s['scores']
        if 'nuclides' in s:tally.nuclides=s['nuclides']
        filters=[]
        for f in s.get('filters',[]):
            if f['type']=='energy':filters.append(openmc.EnergyFilter(f['bins'],filter_id=next_filter_id))
            elif f['type']=='material':filters.append(openmc.MaterialFilter(f['bins'],filter_id=next_filter_id))
            else:
                mesh=openmc.RegularMesh(mesh_id=s['id']);mesh.dimension=f['dimension'];mesh.lower_left=f['lower_left'];mesh.upper_right=f['upper_right'];filters.append(openmc.MeshFilter(mesh,filter_id=next_filter_id))
            next_filter_id+=1
        tally.filters=filters;tallies.append(tally)
    model=openmc.Model(geometry,openmc.Materials(list(materials.values())),settings,tallies)
    model.export_to_xml()
    parsed=openmc.Model.from_xml()
    # Bind retained source to the actual generated XML. Rebuilding edited specs
    # intentionally refreshes these fingerprints; parameter updates check first.
    save_json(Path('model-spec.json'),spec)
    marker=model_metadata(Path('.'))
    save_json(Path('mc-case.json'),marker)
    return {'model_spec_sha256':marker['model_spec_sha256'],'model_xml_sha256':marker['model_xml_sha256'],
            'builder_source_sha256':marker['builder_source_sha256'],
            'openmc_version':openmc.__version__, 'materials':len(parsed.materials),'cells':len(parsed.geometry.get_all_cells()),
            'lattices':len(parsed.geometry.get_all_lattices()),'tallies':len(parsed.tallies),'api_parsed':True,
            'files':[p.name for p in Path('.').glob('*.xml')]}


def save_json(path, value):
    fd,name=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.write('\n')
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_metadata(directory):
    import openmc
    return {'data_kind':'user_supplied_unverified','generation_method':'declarative_public_python_api',
            'openmc_version':openmc.__version__,'schema_version':'1',
            'builder_source_sha256':digest(Path(__file__).resolve()),
            'model_spec_sha256':digest(directory/'model-spec.json'),
            'model_xml_sha256':{p.name:digest(p) for p in directory.glob('*.xml')},
            'fine_tuned_model_used':False,'professional_acceptance':False,
            'note':'用户/宿主显式参数经OpenMC Python API生成；未专业审定。'}


def checked_declarative_spec():
    """Read/hash once per preflight; native models return None."""
    directory=Path.cwd()
    marker_path=directory/'mc-case.json'
    if not marker_path.is_file():return None
    marker=json.loads(marker_path.read_text(encoding='utf-8'))
    if marker.get('generation_method')!='declarative_public_python_api':return None
    expected=marker.get('model_xml_sha256')
    actual={p.name:digest(p) for p in directory.glob('*.xml')}
    spec_path=directory/'model-spec.json'
    if (not expected or actual!=expected or not spec_path.is_file()
            or digest(spec_path)!=marker.get('model_spec_sha256')):
        raise ValueError('声明式模型XML或model-spec已漂移，未覆盖；请重新导入为原生模型或从新spec重建')
    spec=json.loads(spec_path.read_text(encoding='utf-8'))
    validate_spec(spec)
    return spec


def mapped_declarative_spec(baseline,parameters):
    """Pure parameter projection used identically by preflight and generation."""
    spec=deepcopy(baseline)
    materials={m['id']:m for m in spec['materials']}
    surfaces={s['id']:s for s in spec['surfaces']}
    for key,value in parameters.items():
        parts=key.split('.')
        if parts[0]=='materials':materials[int(parts[1])][parts[2]]=value
        elif parts[:2]==['geometry','surfaces']:surfaces[int(parts[2])]['parameters'][parts[3]]=value
        elif parts[:2]==['settings','source']:
            if parts[2:]!=['0','strength']:raise ValueError('model-spec只支持一个源的strength修改')
            spec['settings']['source']['strength']=value
        else:spec['settings'][parts[1]]=value
    validate_spec(spec)
    return spec


def rebuild_parameters(parameters):
    """Keep declarative source and native inputs in sync without running saved code."""
    directory=Path.cwd()
    baseline=checked_declarative_spec()
    if baseline is None:raise ValueError('不是声明式模型')
    spec=mapped_declarative_spec(baseline,parameters)
    marker=json.loads((directory/'mc-case.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='.spec-update-',dir=directory) as tmp:
        staging=Path(tmp)
        try:
            os.chdir(staging)
            build(spec)
        finally:os.chdir(directory)
        # Refresh the generated helper from this trusted installed module. A
        # copied build_model.py is an output artifact, never an execution input.
        shutil.copyfile(Path(__file__).resolve(),staging/'build_model.py')
        generated=json.loads((staging/'mc-case.json').read_text(encoding='utf-8'))
        marker.update(generated)
        save_json(staging/'mc-case.json',marker)
        for name in [*generated['model_xml_sha256'],'model-spec.json','build_model.py','mc-case.json']:
            os.replace(staging/name,directory/name)


if __name__=='__main__':
    print(json.dumps(build(json.loads(Path('model-spec.json').read_text(encoding='utf-8'))),ensure_ascii=False))
