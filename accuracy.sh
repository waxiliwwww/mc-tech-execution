#!/usr/bin/env bash
# 表4-2 准确率比对入口：读参考值清单→相同设置运行→按容差判定→出报告。
# 退出码：0=全部PASS，1=有FAIL，2=有INCONCLUSIVE（缺参考值/未完成等）。
set -euo pipefail
mc_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$mc_root/scripts/accuracy_baseline.py" "$@"
