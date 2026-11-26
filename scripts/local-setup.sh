#!/usr/bin/env bash
# One explicit preparation step; ordinary MCP startup remains offline.
set -euo pipefail
mc_local_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$mc_local_root"
mkdir -p .runtime
exec 9>.runtime/local-setup.lock
flock -n 9 || { printf '已有本地环境准备进程；请等待完成后重试。\n' >&2; exit 2; }
mc_local_quiet=0
mc_local_workspace=''
mc_local_install=0
mc_local_cad=1
mc_local_office=0
mc_local_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace|--openmc-prefix)
      [[ $# -ge 2 && -n "$2" ]] || { printf '%s需要目录参数。\n' "$1" >&2; exit 2; }
      if [[ "$1" == --workspace ]]; then mc_local_workspace="$(realpath -m -- "$2")"
      else mc_local_args+=(--openmc-prefix "$(realpath -m -- "$2")");mc_local_install=1; fi
      shift 2 ;;
    --no-connection-output) mc_local_quiet=1;shift ;;
    --install-runtime) mc_local_install=1;shift ;;
    --with-cad) mc_local_cad=1;shift ;;
    --skip-cad) mc_local_cad=0;shift ;;
    --with-office) mc_local_office=1;shift ;;
    -h|--help)
      printf '用法：bash scripts/local-setup.sh [--workspace 目录] [--openmc-prefix 已有环境] [--install-runtime] [--skip-cad] [--with-office]\n已有个人配置或config/openmc.local.json时默认复用；否则准备基础计算和绘图，首次需网络并下载OpenMC及核数据。默认准备cadquery和cad_to_dagmc转换环境；--skip-cad显式跳过，Office按需安装。不会启动HTTP或修改宿主设置。\n';exit 0 ;;
    *) printf '未知选项：%s\n' "$1" >&2;exit 2 ;;
  esac
done
mc_local_reuse="$(python3 - "$mc_local_root" "$mc_local_workspace" <<'PY'
import os,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'mcp'))
import personal_config
reusable=personal_config.has_runtime(root)
# Seed the previous saved workspace; commit a requested change only after probes pass.
os.environ.pop('MC_WORKSPACE',None)
personal_config.ensure(root)
print('1' if reusable else '0')
PY
)"
if [[ -n "$mc_local_workspace" ]]; then export MC_WORKSPACE="$mc_local_workspace";fi
if [[ "$mc_local_reuse" == 1 && "$mc_local_install" == 0 ]]; then
  printf '复用已有OpenMC配置，准备MCP依赖...\n' >&2
  bash mcp/setup.sh >&2
  mc_local_extras=()
  [[ "$mc_local_cad" == 0 ]] || mc_local_extras+=(--cad)
  [[ "$mc_local_office" == 0 ]] || mc_local_extras+=(--office)
  if [[ ${#mc_local_extras[@]} -gt 0 ]]; then mcp/.venv/bin/python scripts/install_local_extras.py "${mc_local_extras[@]}" >&2;fi
else
  [[ "$mc_local_cad" == 1 ]] || mc_local_args+=(--skip-cad)
  [[ "$mc_local_office" == 1 ]] || mc_local_args+=(--skip-office)
  bash quickstart.sh "${mc_local_args[@]}" >&2
fi
mcp/.venv/bin/python - "$mc_local_root" "$mc_local_workspace" "$mc_local_quiet" <<'PY'
import json,os,sys,tempfile
from pathlib import Path
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'mcp'))
import personal_config
workspace=personal_config.workspace(root)
os.environ['MC_WORKSPACE']=str(workspace)
# Validate the existing runtime config without changing it or running a solver.
sys.path.insert(0,str(root/'mcp'))
from openmc_tools import environment
state=environment()
if not state.get('api_available') or not state.get('executable_available') or not state.get('openmc_version_matches'):
 raise SystemExit('环境尚未就绪；检查个人配置runtime中的python_command/openmc_command及OpenMC版本，或运行--install-runtime。原配置保留。')
workspace.mkdir(parents=True,exist_ok=True)
from openmc_import import publish_directory
try:
 with tempfile.TemporaryDirectory(prefix='.mc-setup-',dir=workspace) as probe:
  source=Path(probe)/'source';source.mkdir()
  publish_directory(source,Path(probe)/'published')
except ValueError as error:
 raise SystemExit('工作区不支持所需的Linux目录原子发布；请用--workspace选择Linux文件系统中的目录，原配置保留。'+str(error))
def save(path,value):
 pending=path.with_suffix(path.suffix+'.tmp')
 pending.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');pending.chmod(0o600);pending.replace(path)
personal_config.update({'workspace':str(workspace)},root)
entry={'command':'/bin/bash','args':[str(root/'mcp/stdio.sh')]}
save(root/'config/stdio-connection.local.json',{'mcpServers':{'openmc-forge':entry}})
print('准备完成。个人配置：'+str(personal_config.location()))
print('工作区：'+str(workspace))
sys.path.insert(0,str(root/'scripts'))
from install_local_extras import cad_status
from openmc_runtime import config
print(cad_status(config()))
if sys.argv[3] != '1':
 print('宿主连接配置（按宿主要求导入，不自动修改）：')
 print(json.dumps({'mcpServers':{'openmc-forge':entry}},ensure_ascii=False,indent=2))
print('HTTP部署仍使用：bash mcp/start.sh')
PY
