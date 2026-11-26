"""One optional personal configuration; reads never create or migrate user files."""
from __future__ import annotations
import copy,json,os,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTTP_FIELDS=('host','port','path','name','clientName','publicUrl')


def location(env=None) -> Path:
    env=os.environ if env is None else env
    explicit=env.get('MC_PERSONAL_CONFIG')
    base=Path(env.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    return Path(explicit).expanduser().resolve() if explicit else (base/'openmc-forge/config.json').expanduser().resolve()


def read(env=None) -> dict:
    path=location(env)
    if not path.is_file():return {}
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('个人配置须为JSON对象：'+str(path))
    return value


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def workspace(root=ROOT) -> Path:
    root=Path(root);explicit=os.environ.get('MC_WORKSPACE')
    if explicit:return Path(explicit).expanduser().resolve()
    value=read().get('workspace')
    if not value:
        old=root/'config/launcher.local.json'
        value=_json(old).get('workspace') if old.is_file() else None
    chosen=Path(value or 'workspace').expanduser()
    return (root/chosen).resolve()


def logs(root=ROOT,active_workspace=None) -> Path:
    root=Path(root);data=read()
    if data.get('log_directory'):return Path(data['log_directory']).expanduser().resolve()
    if location().is_file():return Path(active_workspace or workspace(root))/'.mc-logs'
    return Path(__file__).resolve().parent/'logs' if root==ROOT else root/'mcp/logs'


def defaults_path(root=ROOT):
    # Same source / pip --target / normal prefix layouts as workflow_guidance.
    import sysconfig
    relative=Path('share/mc-agent-tools/config/openmc.json')
    for path in (Path(root)/'config/openmc.json',Path(root)/relative,Path(sysconfig.get_path('data'))/relative):
        if path.is_file():return path
    return Path(root)/'config/openmc.json'


def runtime(root=ROOT,env=None) -> dict:
    env=os.environ if env is None else env
    root=Path(root);explicit=env.get('MC_OPENMC_CONFIG')
    if explicit:
        path=Path(explicit);return _json(path if path.is_absolute() else root/path)
    data=read(env)
    old=root/'config/openmc.local.json'
    if 'runtime' not in data:
        return _json(old if old.is_file() else defaults_path(root))
    base=_json(defaults_path(root))
    if old.is_file():
        # A migrated personal file is authoritative; an obsolete broken file
        # must not prevent it from running. Valid old fields still fill gaps.
        try:
            previous=_json(old)
            if isinstance(previous,dict):base.update(previous)
        except (OSError,ValueError):pass
    return {**base,**copy.deepcopy(data['runtime'])}


def has_runtime(root=ROOT) -> bool:
    values=read().get('runtime') or {}
    return bool(os.environ.get('MC_OPENMC_CONFIG') or values.get('python_command') and values.get('openmc_command') or (Path(root)/'config/openmc.local.json').is_file())


def slurm() -> dict | None:
    source=os.environ.get('MC_SLURM_CONFIG')
    return _json(source) if source else copy.deepcopy(read().get('slurm') or None)


def has_slurm():
    return bool(os.environ.get('MC_SLURM_CONFIG')) or bool(read().get('slurm'))


def transfer() -> dict:
    source=os.environ.get('MC_TRANSFER_CONFIG')
    return _json(source) if source else copy.deepcopy(read().get('transfer') or {})


def http(env=None) -> dict:
    return {k:v for k,v in (read(env).get('http') or {}).items() if k in HTTP_FIELDS}


def _atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def _normalize_runtime(value,root):
    value=copy.deepcopy(value)
    for key in ('cross_sections','depletion_chain','font_path','cad_library_path','nuclear_data_directory','nuclear_data_root'):
        if value.get(key):value[key]=str((Path(root)/Path(value[key]).expanduser()).resolve())
    for key in ('python_command','openmc_command','cad_python_command','office_command','cmake_command'):
        argv=value.get(key)
        if isinstance(argv,list) and argv and '/' in argv[0] and not Path(argv[0]).is_absolute():argv[0]=str((Path(root)/Path(argv[0]).expanduser()).absolute())
    value.setdefault('nuclear_data_root',str((Path(root)/'.runtime/nuclear-data').resolve()))
    return value


def legacy(root=ROOT) -> dict:
    root=Path(root);value={'workspace':str(workspace(root))}
    source=os.environ.get('MC_OPENMC_CONFIG')
    old=(root/Path(source)) if source else root/'config/openmc.local.json'
    value['runtime']=_normalize_runtime(_json(old),root) if old.is_file() else {}
    for key,env in [('slurm','MC_SLURM_CONFIG'),('transfer','MC_TRANSFER_CONFIG')]:
        if os.environ.get(env):value[key]=_json(os.environ[env])
    service=Path(os.environ.get('MCP_CONFIG_FILE') or root/'mcp/service.config.json')
    if not service.is_absolute():service=root/'mcp'/service
    if service.is_file():value['http']={k:v for k,v in _json(service).items() if k in HTTP_FIELDS}
    return value


def update(changes,root=ROOT,seed=True):
    """Merge under one lock, preserve other sections and publish atomically."""
    import fcntl
    path=location();path.parent.mkdir(parents=True,exist_ok=True)
    with Path(str(path)+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        value=read() if path.is_file() else legacy(root) if seed else {}
        for key,change in changes.items():
            if isinstance(change,dict) and isinstance(value.get(key),dict):value[key]={**value[key],**copy.deepcopy(change)}
            else:value[key]=copy.deepcopy(change)
        _atomic(path,value)
    return value


def ensure(root=ROOT,selected_workspace=None):
    changes={}
    if selected_workspace:changes['workspace']=str(Path(selected_workspace).expanduser().resolve())
    return update(changes,root)


def runtime_target(root=ROOT,explicit=None):
    source=explicit or os.environ.get('MC_OPENMC_CONFIG')
    if source:return Path(root)/Path(source)
    return location() if location().is_file() else Path(root)/'config/openmc.local.json'


def save_runtime(value,root=ROOT,explicit=None):
    source=explicit or os.environ.get('MC_OPENMC_CONFIG')
    if source:
        path=Path(source);_atomic(path if path.is_absolute() else Path(root)/path,value)
    elif location().is_file():update({'runtime':_normalize_runtime(value,root)},root)
    else:_atomic(Path(root)/'config/openmc.local.json',value)


def summary(root=ROOT,active_workspace=None,cfg=None):
    root=Path(root);cfg=cfg or runtime(root);settings=http()
    try:cluster=slurm() or {}
    except (OSError,ValueError):cluster={}
    if not isinstance(cluster,dict):cluster={}
    from file_transfer import policy
    try:transfers=policy()
    except ValueError:transfers={}
    return {'file':str(location()),'source':'personal' if location().is_file() else 'legacy',
            'workspace':str(active_workspace or workspace(root)),
            'log_directory':str(logs(root,active_workspace)),
            'runtime':{k:(cfg[k][:1] if k.endswith('_command') else cfg[k]) for k in ('python_command','openmc_command','cad_python_command','office_command','cross_sections','nuclear_data_directory','nuclear_data_root','depletion_chain','max_concurrent_jobs','cpu_count','api_timeout_seconds','job_timeout_seconds','min_free_disk_bytes','max_input_bytes','report_timeout_seconds','cad_timeout_seconds','geometry_check_timeout_seconds','max_log_bytes','max_scan_cases','max_plot_points','max_report_rows') if k in cfg},
            'slurm':{**{k:cluster[k] for k in ('transport','host','remote_root','cross_sections') if k in cluster},**({'resource_defaults':{k:v for k,v in cluster['resource_defaults'].items() if k in ('nodes','ntasks','cpus_per_task','partition','time','memory_mb','account','qos')}} if isinstance(cluster.get('resource_defaults'),dict) else {})},
            'overrides':{k:os.environ[k] for k in ('MC_WORKSPACE','MC_OPENMC_CONFIG','MC_SLURM_CONFIG','MC_TRANSFER_CONFIG','MCP_CONFIG_FILE','MCP_HOST','MCP_PORT','MCP_PATH') if k in os.environ},
            'http':{k:settings[k] for k in ('host','port','path') if k in settings},
            'transfer':{k:v for k,v in transfers.items() if k.startswith(('max_','min_','preview_')) or k=='chunk_bytes'},
            'note':'常用设置在同一个人文件；Agent可用此Linux节点已有文件/终端能力修改，用户可手改。连接/工作区/日志设置修改后重新连接生效；既有作业保存当次设置。模型材料、粒子数和批次仍在算例内。'}
