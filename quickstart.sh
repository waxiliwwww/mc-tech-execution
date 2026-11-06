#!/usr/bin/env bash
# Prepare runtime dependencies once. Never edit shell profiles or install system packages.
set -euo pipefail
mc_quick_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$mc_quick_root"
mc_quick_prefix="$mc_quick_root/.runtime/openmc"
mc_quick_mamba="$mc_quick_root/.runtime/install/bin/micromamba"
mc_quick_office=1
mc_quick_cad=1
mc_quick_cad_only=0
mc_quick_cad_prefix="$mc_quick_root/.runtime/cadenv"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --openmc-prefix)
      [[ $# -gt 1 && -n "$2" && "$2" != --* ]] || { printf -- '--openmc-prefix需要目录参数。\n' >&2; exit 2; }
      mc_quick_prefix="$(realpath -m -- "$2")"; shift 2 ;;
    --skip-office) mc_quick_office=0; shift ;;
    --skip-cad) mc_quick_cad=0; shift ;;
    --cad-only) mc_quick_cad_only=1; shift ;;
    --cad-prefix)
      [[ $# -gt 1 && -n "$2" && "$2" != --* ]] || { printf -- '--cad-prefix需要目录参数。\n' >&2; exit 2; }
      mc_quick_cad_prefix="$(realpath -m -- "$2")"; shift 2 ;;
    -h|--help) printf '用法：./quickstart.sh [--openmc-prefix 已有OpenMC环境] [--cad-prefix CAD环境目录] [--skip-cad] [--skip-office]\n补装CAD：./quickstart.sh --cad-only [--openmc-prefix 已有环境] [--cad-prefix 新CAD目录]\n默认准备CLI/MCP、OpenMC、图表、独立CAD转换环境及Word/PDF转换；只写项目目录。--cad-only不重复准备OpenMC、核数据或Office，不可与--skip-cad同时使用。\n'; exit 0 ;;
    *) printf '未知选项：%s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$mc_quick_cad_only" == 0 || "$mc_quick_cad" == 1 ]] || { printf -- '--cad-only与--skip-cad不能同时使用。\n' >&2; exit 2; }
[[ "$(uname -s)" == Linux ]] || { printf '服务端请使用Linux/WSL；其他系统可作为MCP客户端。\n' >&2; exit 1; }
if [[ "$mc_quick_cad_only" == 1 ]]; then
  [[ -x "$mc_quick_prefix/bin/python" ]] || { printf '补装CAD需要已有OpenMC Python；先完成基础初配，或指定--openmc-prefix。\n' >&2; exit 1; }
  printf '[1/1] 仅准备CAD环境并实测STEP到H5M；保留其他运行配置...\n'
  "$mc_quick_prefix/bin/python" scripts/install_cad.py --python "$mc_quick_prefix/bin/python" --prefix "$mc_quick_cad_prefix"
  exit 0
fi
command -v g++ > /dev/null || { printf '初配需要C++编译器（gcc/g++与make）；请在系统环境准备后重试。\n' >&2; exit 1; }
command -v make > /dev/null || { printf '初配需要make；请先准备系统构建工具。\n' >&2; exit 1; }
printf '[1/5] 准备独立CLI与MCP环境...\n'
./mcp/setup.sh
if [[ ! -x "$mc_quick_prefix/bin/openmc" || ! -x "$mc_quick_prefix/bin/python" ]]; then
  printf '[2/5] 准备项目内OpenMC 0.16.0（首次需网络）...\n'
  mc_quick_mamba="$mc_quick_root/.runtime/install/bin/micromamba"
  if [[ ! -x "$mc_quick_mamba" ]]; then
    command -v curl > /dev/null
    case "$(uname -m)" in x86_64) mc_quick_platform=linux-64 ;; aarch64) mc_quick_platform=linux-aarch64 ;; *) printf '此架构请自行安装OpenMC，再指定--openmc-prefix。\n' >&2; exit 1 ;; esac
    mkdir -p .runtime/install
    curl --fail --location --retry 2 "https://micro.mamba.pm/api/micromamba/$mc_quick_platform/latest" --output .runtime/install/micromamba.tar.bz2
    tar -xjf .runtime/install/micromamba.tar.bz2 -C .runtime/install bin/micromamba
  fi
  if [[ "$(uname -m)" == x86_64 ]]; then
    "$mc_quick_mamba" --no-rc -r "$mc_quick_root/.runtime/mamba-root" create -y -p "$mc_quick_prefix" --file config/openmc-linux-64.lock
  else
    "$mc_quick_mamba" --no-rc -r "$mc_quick_root/.runtime/mamba-root" create -y -p "$mc_quick_prefix" --override-channels -c conda-forge 'python=3.12' 'openmc=0.16.0' 'openmc=*=*dagmc_nompi*' 'eigen=3.4' njoy2016
  fi
else
  printf '[2/5] 检查已有OpenMC环境...\n'
fi
if [[ ! -d "$mc_quick_prefix/include/eigen3" ]]; then
  if [[ -x "$mc_quick_mamba" ]]; then
    printf '[2/5] 补装 Eigen 头文件（C++ 自定义源编译需要）...\n'
    "$mc_quick_mamba" --no-rc -r "$mc_quick_root/.runtime/mamba-root" install -y -p "$mc_quick_prefix" eigen=3.4
  fi
fi
[[ -d "$mc_quick_prefix/include/eigen3" ]] || {
  printf 'OpenMC 环境缺少 Eigen 头文件（%s/include/eigen3）。C++ 自定义源编译会失败；请在环境中安装 eigen=3.4，或用默认 quickstart 重新准备。\n' "$mc_quick_prefix" >&2
  exit 1
}
python3 mcp/configure_openmc.py --prefix "$mc_quick_prefix"
printf '[3/5] 准备图表/报告Python依赖...\n'
"$mc_quick_prefix/bin/python" -m pip install --disable-pip-version-check -r config/report-requirements.txt
"$mc_quick_prefix/bin/python" -m pip install --disable-pip-version-check -r config/example-requirements.txt
"$mc_quick_prefix/bin/python" -m pip check
"$mc_quick_prefix/bin/python" scripts/install_openmoc.py
./setup-example-sources.sh
printf '初次准备官方多温度核数据库（约1.7GB下载、6.3GB展开）...\n'
python3 scripts/install_official_data.py
python3 - "$mc_quick_prefix" <<'PY'
import json,sys
from pathlib import Path
p=Path('config/openmc.local.json');v=json.loads(p.read_text());v['nuclear_data_library']='official-endfb-vii.1'
v['cmake_command']=[str(Path(sys.argv[1])/'bin/cmake')]
p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
PY
if [[ "$mc_quick_cad" == 1 ]]; then
  printf '[4/5] 准备CAD转换环境并实测STEP到H5M...\n'
  "$mc_quick_prefix/bin/python" scripts/install_cad.py --python "$mc_quick_prefix/bin/python" --prefix "$mc_quick_cad_prefix"
else
  printf '[4/5] 按选项跳过CAD环境；转换功能须另行配置cad_python_command。\n'
fi
if [[ "$mc_quick_office" == 1 ]]; then
  printf '[5/5] 准备Word/PDF转换及字体...\n'
  if command -v soffice > /dev/null; then
    mc_quick_office_command="$(command -v soffice)"
  else
    python3 scripts/install_office.py
    mc_quick_office_command="$mc_quick_root/.runtime/office/soffice"
  fi
  python3 - "$mc_quick_office_command" <<'PY'
import json,os,sys
from pathlib import Path
p=Path('config/openmc.local.json'); config=json.loads(p.read_text())
config['office_command']=[sys.argv[1]]
for base in [Path('.runtime/office/usr/share/fonts'), Path('/usr/share/fonts')]:
    matches=list(base.rglob('NotoSansCJK-Regular.ttc'))
    if matches:config['font_path']=str(matches[0].resolve()); break
p.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n');os.chmod(p,0o600)
PY
else
  printf '[5/5] 按选项跳过Office准备；PDF/DOC须另行配置，DOCX无需转换组件。\n'
fi
./mc.sh call openmc_environment
printf '\n环境准备完成。下一步：\n'
printf '  1) 启动 MCP 服务（前台保持运行）：./mcp/start.sh\n'
printf '     启动输出会给出地址、版本、传输/鉴权、工具数量和事件日志路径。\n'
printf '  2) 按宿主要求填写 URL、传输和认证，把 MCP 接到 DSH 或其他客户端；接入示例见 docs/deployment.html#handoff。\n'
printf '  3) 在 DSH 中先调用 mcp__openmc-forge__openmc_environment，再试一句：\n'
printf '     “用 MCP 跑官方例题 source-pincell，新目录 cases/my-trial/pincell，按 next_step 逐步执行并读结果。”\n'
printf '\n文档入口：%s/docs/index.html\n逐步计算：docs/compute.html\n快速例题：mcp/.venv/bin/python examples/public_case_demo.py\n' "$mc_quick_root"
