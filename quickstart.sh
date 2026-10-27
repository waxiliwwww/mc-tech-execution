#!/usr/bin/env bash
# Prepare runtime dependencies once. Never edit shell profiles or install system packages.
set -euo pipefail
mc_quick_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$mc_quick_root"
mc_quick_prefix="$mc_quick_root/.runtime/openmc"
mc_quick_office=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --openmc-prefix) mc_quick_prefix="$(realpath "$2")"; shift 2 ;;
    --skip-office) mc_quick_office=0; shift ;;
    -h|--help) printf '用法：./quickstart.sh [--openmc-prefix 已有OpenMC环境] [--skip-office]\n默认准备CLI/MCP、OpenMC、图表及Word/PDF转换；只写项目目录。\n'; exit 0 ;;
    *) printf '未知选项：%s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$(uname -s)" == Linux ]] || { printf '服务端请使用Linux/WSL；其他系统可作为MCP客户端。\n' >&2; exit 1; }
printf '[1/4] 准备独立CLI与MCP环境...\n'
./mcp/setup.sh
if [[ ! -x "$mc_quick_prefix/bin/openmc" || ! -x "$mc_quick_prefix/bin/python" ]]; then
  printf '[2/4] 准备项目内OpenMC 0.16.0（首次需网络）...\n'
  mc_quick_mamba="$mc_quick_root/.runtime/install/bin/micromamba"
  if [[ ! -x "$mc_quick_mamba" ]]; then
    command -v curl > /dev/null
    case "$(uname -m)" in x86_64) mc_quick_platform=linux-64 ;; aarch64) mc_quick_platform=linux-aarch64 ;; *) printf '此架构请自行安装OpenMC，再指定--openmc-prefix。\n' >&2; exit 1 ;; esac
    mkdir -p .runtime/install
    curl --fail --location --retry 2 "https://micro.mamba.pm/api/micromamba/$mc_quick_platform/latest" --output .runtime/install/micromamba.tar.bz2
    tar -xjf .runtime/install/micromamba.tar.bz2 -C .runtime/install bin/micromamba
  fi
  "$mc_quick_mamba" --no-rc -r "$mc_quick_root/.runtime/mamba-root" create -y -p "$mc_quick_prefix" --override-channels -c conda-forge 'python=3.12' 'openmc=0.16.0' 'openmc=*=*nompi*'
else
  printf '[2/4] 检查已有OpenMC环境...\n'
fi
python3 mcp/configure_openmc.py --prefix "$mc_quick_prefix"
printf '[3/4] 准备图表/报告Python依赖...\n'
"$mc_quick_prefix/bin/python" -m pip install --disable-pip-version-check -r config/report-requirements.txt
"$mc_quick_prefix/bin/python" -m pip check
if [[ "$mc_quick_office" == 1 ]]; then
  printf '[4/4] 准备Word/PDF转换及字体...\n'
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
  printf '[4/4] 按选项跳过Office准备；PDF/DOC须另行配置，DOCX无需转换组件。\n'
fi
./mc.sh call openmc_environment
printf '\n环境准备完成。运行完整示例：\n  mcp/.venv/bin/python examples/scan_report_demo.py\n启动MCP：\n  ./mcp/start.sh\n首次准备需要软件源网络；日常计算无需连接其他项目目录。\n'
