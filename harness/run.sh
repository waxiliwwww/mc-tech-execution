#!/usr/bin/env bash
# 项目启动器：不带 --config 时默认使用同目录的 model.local.json（存在才用）。
# 没有该文件（例如打包分发出去的副本）时，退回终端的 MC_PRIMARY_* / MC_FALLBACK_* 环境变量模式。
set -euo pipefail
mc_harness_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mc_harness_config_given=""
for mc_harness_arg in "$@"; do
  if [[ "$mc_harness_arg" == "--config" ]]; then mc_harness_config_given=1; break; fi
done
if [[ -z "$mc_harness_config_given" && -f "$mc_harness_root/model.local.json" ]]; then
  set -- --config "$mc_harness_root/model.local.json" "$@"
fi
exec node "$mc_harness_root/terminal.mjs" "$@"